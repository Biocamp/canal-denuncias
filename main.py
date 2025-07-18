import os
import uuid
import secrets
import logging
from flask import Flask, redirect, url_for, session, request, render_template, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message as MailMessage
from functools import wraps
from threading import Thread
from werkzeug.middleware.proxy_fix import ProxyFix

# --- App e Logging ---
app = Flask(__name__)
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
logging.basicConfig(level=logging.DEBUG)

# --- Configurações de Segurança e Banco de Dados ---
app.secret_key = os.environ.get('FLASK_SECRET', 'segredosuperseguro@123').strip()
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///database.db').strip()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Upload ---
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
ALLOWED_EXTENSIONS = {
    'png','jpg','jpeg','gif','pdf','doc','docx','xls','xlsx',
    'mp3','wav','ogg','mp4','webm','mov'
}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Flask-Mail via ENV ---
app.config.update(
    MAIL_SERVER   = os.environ.get('MAIL_SERVER', '').strip(),
    MAIL_PORT     = int(os.environ.get('MAIL_PORT', 465)),
    MAIL_USE_SSL  = True,
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '').strip(),
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '').strip(),
)
mail = Mail(app)
db   = SQLAlchemy(app)

# --- Models ---
class Denuncia(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    texto      = db.Column(db.Text, nullable=False)
    data_hora  = db.Column(db.DateTime, server_default=db.func.now())
    protocolo  = db.Column(db.String(20), unique=True, nullable=False)
    status     = db.Column(db.String(30), default='Recebida')
    observacao = db.Column(db.Text, nullable=True)

class MensagemChat(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    denuncia_id  = db.Column(db.Integer, db.ForeignKey('denuncia.id'), nullable=False)
    autor        = db.Column(db.String(30), nullable=False)
    texto        = db.Column(db.Text, nullable=True)
    data_hora    = db.Column(db.DateTime, server_default=db.func.now())
    anexo        = db.Column(db.String(120), nullable=True)
    lida_pelo_rh = db.Column(db.Boolean, default=False)
    denuncia     = db.relationship('Denuncia', backref=db.backref('mensagens', lazy=True))

with app.app_context():
    db.create_all()

# --- Carrega listas de e-mails RH e Admin (exatamente das VARS do Railway) ---
raw_rh    = os.environ.get('RH_EMAIL', '').strip()
RH_EMAILS = [e.lower() for e in raw_rh.split(',') if e.strip()]

raw_admin    = os.environ.get('ADMIN_EMAIL', '').strip()
ADMIN_EMAILS = [e.lower() for e in raw_admin.split(',') if e.strip()]

# DEBUG: confira nos logs se está tudo correto
app.logger.debug(f"RAW_RH       = {raw_rh!r}")
app.logger.debug(f"Parsed RH    = {RH_EMAILS}")
app.logger.debug(f"RAW_ADMIN    = {raw_admin!r}")
app.logger.debug(f"Parsed ADMIN = {ADMIN_EMAILS}")

# --- Autorizados adicionais (arquivo autorizados.txt) ---
def carregar_emails_autorizados(arquivo='autorizados.txt'):
    if not os.path.exists(arquivo):
        return []
    with open(arquivo, 'r', encoding='utf-8') as f:
        return [linha.strip().lower() for linha in f if linha.strip()]

EMAILS_AUTORIZADOS = carregar_emails_autorizados()

# --- Envio de E-mail ao RH ---
def _send_async_email(app, msg):
    with app.app_context():
        mail.send(msg)

def notify_rh(texto_denuncia, protocolo):
    for rh in RH_EMAILS:
        msg = MailMessage(
            subject='Nova denúncia recebida',
            sender=app.config['MAIL_USERNAME'],
            recipients=[rh],
            body=f"Uma nova denúncia foi registrada:\n\nProtocolo: {protocolo}\n\n{texto_denuncia}"
        )
        Thread(target=_send_async_email, args=(app, msg)).start()

# --- Decorators de permissão ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        allowed = set(EMAILS_AUTORIZADOS + RH_EMAILS + ADMIN_EMAILS)
        if not user or user.get('email','').lower() not in allowed:
            flash('Acesso restrito apenas para usuários autorizados.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_pin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        if user and user.get('email','').lower() in RH_EMAILS + ADMIN_EMAILS:
            if session.get('pending_pin') or not session.get('admin_verified'):
                return redirect(url_for('admin_verificacao'))
            return f(*args, **kwargs)
        flash('Acesso restrito ao RH.', 'danger')
        return redirect(url_for('login'))
    return decorated_function

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Rotas ---
@app.route('/admin_verificacao', methods=['GET','POST'])
@login_required
@admin_pin_required
def admin_verificacao():
    user = session.get('user')
    if not user or user.get('email','').lower() not in RH_EMAILS + ADMIN_EMAILS:
        flash('Acesso restrito ao RH.', 'danger')
        return redirect(url_for('login'))
    if request.method == 'POST':
        pin_digitado = request.form.get('pin','').strip()
        pin_correto  = os.environ.get('ADMIN_PIN','123456').strip()
        if pin_digitado == pin_correto:
            session.pop('pending_pin', None)
            session['admin_verified'] = True
            return redirect(url_for('admin'))
        flash('PIN incorreto.', 'danger')
    return render_template('admin_verificacao.html')

@app.route('/')
@login_required
def index():
    return redirect(url_for('denuncia'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        app.logger.debug(f"EMAIL DIGITADO: {email}")
        if email in EMAILS_AUTORIZADOS + RH_EMAILS + ADMIN_EMAILS:
            session['user'] = {'email': email}
            if email in RH_EMAILS + ADMIN_EMAILS:
                session['pending_pin'] = True
                session.pop('admin_verified', None)
                return redirect(url_for('admin_verificacao'))
            return redirect(url_for('denuncia'))
        flash('E-mail não autorizado.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/denuncia', methods=['GET','POST'])
@login_required
def denuncia():
    if request.method == 'POST':
        if not request.form.get('terms'):
            flash('Você precisa aceitar os termos para prosseguir.', 'warning')
            return redirect(url_for('denuncia'))

        texto = request.form['texto']
        file  = request.files.get('anexo')
        anexo_nome = None
        if file and file.filename and allowed_file(file.filename):
            ext = file.filename.rsplit('.',1)[1].lower()
            anexo_nome = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(UPLOAD_FOLDER, anexo_nome))

        protocolo = secrets.token_hex(6).upper()
        nova = Denuncia(texto=texto, protocolo=protocolo, status='Recebida')
        db.session.add(nova)
        db.session.commit()

        if anexo_nome:
            msg_chat = MensagemChat(
                denuncia_id=nova.id,
                autor="Usuário",
                texto="Anexo inicial da denúncia.",
                anexo=anexo_nome,
                lida_pelo_rh=False
            )
            db.session.add(msg_chat)
            db.session.commit()

        notify_rh(texto, protocolo)
        flash(f'Denúncia enviada com sucesso! Protocolo: {protocolo}', 'success')
        return redirect(url_for('denuncia'))

    return render_template('denuncia.html')

# … se tiver outras rotas (admin, chat, download de anexos), mantenha-as aqui …

if __name__ == '__main__':
    app.run(debug=True)
