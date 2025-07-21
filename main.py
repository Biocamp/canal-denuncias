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
import base64  # ADICIONADO PARA ÁUDIO

# --- App e Logging ---
app = Flask(__name__)
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
logging.basicConfig(level=logging.DEBUG)
logging.getLogger().info("🚩 APP_VERSION: consulta-v2 🚩")

# --- Configurações de Segurança e Banco de Dados ---
app.secret_key = os.environ.get('FLASK_SECRET', 'segredosuperseguro@123').strip()
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///database.db').strip()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Upload ---
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
ALLOWED_EXTENSIONS = {'png','jpg','jpeg','gif','pdf','doc','docx','xls','xlsx','mp3','wav','ogg','mp4','webm','mov'}
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

# --- Carrega listas de e-mails RH e Admin ---
raw_rh       = os.environ.get('RH_EMAIL', '')
RH_EMAILS    = [e.strip().lower() for e in raw_rh.split(',') if e.strip()]
raw_admin    = os.environ.get('ADMIN_EMAIL', '')
ADMIN_EMAILS = [e.strip().lower() for e in raw_admin.split(',') if e.strip()]
app.logger.debug(f"RAW_RH from ENV: {raw_rh!r}")
app.logger.debug(f"Parsed RH_EMAILS: {RH_EMAILS}")

# --- Autorizados adicionais ---
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
        if rh:
            msg = MailMessage(
                subject='Nova denúncia recebida',
                sender=app.config['MAIL_USERNAME'],
                recipients=[rh],
                body=f"Uma nova denúncia foi registrada:\n\nProtocolo: {protocolo}\n\n{texto_denuncia}"
            )
            Thread(target=_send_async_email, args=(app, msg)).start()

# --- Decorators ---
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user   = session.get('user')
        allowed = set(EMAILS_AUTORIZADOS + RH_EMAILS + ADMIN_EMAILS)
        if not user or user['email'].lower() not in allowed:
            flash('Acesso restrito apenas para usuários autorizados.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_pin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = session.get('user')
        if user and user['email'].lower() in RH_EMAILS + ADMIN_EMAILS:
            if session.get('pending_pin') or not session.get('admin_verified'):
                return redirect(url_for('admin_verificacao'))
            return f(*args, **kwargs)
        flash('Acesso restrito ao RH.', 'danger')
        return redirect(url_for('login'))
    return decorated

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

# --- Rotas ---
@app.route('/admin_verificacao', methods=['GET','POST'])
@login_required
def admin_verificacao():
    user = session.get('user')
    if not user or user['email'].lower() not in RH_EMAILS + ADMIN_EMAILS:
        flash('Acesso restrito ao RH.', 'danger')
        return redirect(url_for('login'))
    if request.method == 'POST':
        pin_correct = os.environ.get('ADMIN_PIN', '123456').strip()
        pin_given   = request.form.get('pin','').strip()
        if pin_given == pin_correct:
            session.pop('pending_pin', None)
            session['admin_verified'] = True
            return redirect(url_for('admin'))
        flash('PIN incorreto.','danger')
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
        if email in EMAILS_AUTORIZADOS or email in RH_EMAILS or email in ADMIN_EMAILS:
            session['user'] = {'email': email}
            if email in RH_EMAILS + ADMIN_EMAILS:
                session['pending_pin'] = True
                session.pop('admin_verified', None)
                return redirect(url_for('admin_verificacao'))
            return redirect(url_for('denuncia'))
        flash('E-mail não autorizado.','danger')
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
            flash('Você precisa aceitar os termos e condições.','warning')
            return render_template('denuncia.html')
        texto = request.form['texto']
        file  = request.files.get('anexo')
        anexo = None
        if file and file.filename and allowed_file(file.filename):
            ext   = file.filename.rsplit('.',1)[1].lower()
            anexo = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(UPLOAD_FOLDER, anexo))
        protocolo = secrets.token_hex(6).upper()
        d = Denuncia(texto=texto, protocolo=protocolo)
        db.session.add(d)
        db.session.commit()
        if anexo:
            m = MensagemChat(denuncia_id=d.id, autor='Usuário', texto=None, anexo=anexo, lida_pelo_rh=False)
            db.session.add(m)
            db.session.commit()
        notify_rh(texto, protocolo)
        flash('Denúncia enviada com sucesso! Anote o protocolo abaixo.', 'success')
        return render_template('denuncia.html', protocolo=protocolo)
    return render_template('denuncia.html')

@app.route('/consulta', methods=['GET','POST'])
def consulta():
    app.logger.debug(f"[DEBUG] consulta() chamado — method={request.method}, form={request.form}, args={request.args}")
    denuncia = None
    msgs     = []
    proto    = None
    if request.method == 'POST':
        proto = request.form.get('protocolo','').strip().upper()
    elif 'protocolo' in request.args:
        proto = request.args.get('protocolo','').strip().upper()
    if proto:
        denuncia = Denuncia.query.filter_by(protocolo=proto).first()
        if not denuncia:
            flash('Protocolo não encontrado.','warning')
        else:
            msgs = MensagemChat.query.filter_by(denuncia_id=denuncia.id).order_by(MensagemChat.data_hora.asc()).all()
    return render_template('consulta.html', denuncia=denuncia, mensagens=msgs, protocolo=proto)

@app.route('/chat/<protocolo>', methods=['POST'])
def chat(protocolo):
    d = Denuncia.query.filter_by(protocolo=protocolo).first()
    if not d:
        flash('Protocolo não existe.','danger')
        return redirect(url_for('consulta'))
    if d.status == 'Finalizada':
        flash('Conversa encerrada.','danger')
        return redirect(url_for('consulta', protocolo=protocolo))
    texto = request.form.get('mensagem','').strip()
    file  = request.files.get('anexo')
    anexo = None

    # --- SUPORTE AO ÁUDIO COMO ANEXO ---
    audio_data = request.form.get("audio_blob")
    if audio_data and audio_data.startswith("data:audio"):
        header, encoded = audio_data.split(",", 1)
        audio_bytes = base64.b64decode(encoded)
        ext = ".webm"
        fname = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOAD_FOLDER, fname), "wb") as f:
            f.write(audio_bytes)
        anexo = fname  # Usa o áudio como anexo

    # --- Se não houver áudio, tenta salvar anexo normal ---
    if not anexo and file and file.filename and allowed_file(file.filename):
        ext   = file.filename.rsplit('.',1)[1].lower()
        anexo = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(UPLOAD_FOLDER, anexo))

    if texto or anexo:
        m = MensagemChat(denuncia_id=d.id, autor='Usuário', texto=texto or None, anexo=anexo, lida_pelo_rh=False)
        db.session.add(m)
        db.session.commit()
    return redirect(url_for('consulta', protocolo=protocolo))

@app.route('/chat_arquivo/<filename>')
def chat_arquivo(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/admin', methods=['GET','POST'])
@login_required
@admin_pin_required
def admin():
    subq = db.session.query(
        MensagemChat.denuncia_id.label('denuncia_id'),
        db.func.count().label('novas_msgs')
    ).filter(
        MensagemChat.autor=='Usuário',
        MensagemChat.lida_pelo_rh==False
    ).group_by(MensagemChat.denuncia_id).subquery()
    rows = db.session.query(Denuncia, subq.c.novas_msgs)\
        .outerjoin(subq, Denuncia.id==subq.c.denuncia_id)\
        .order_by(Denuncia.data_hora.desc()).all()
    denuncias     = [r[0] for r in rows]
    unread_counts = {r[0].protocolo: int(r[1] or 0) for r in rows}
    return render_template('admin.html', denuncias=denuncias, unread_counts=unread_counts)

@app.route('/admin/denuncia/<protocolo>', methods=['GET','POST'])
@login_required
@admin_pin_required
def admin_denuncia(protocolo):
    d    = Denuncia.query.filter_by(protocolo=protocolo).first_or_404()
    msgs = MensagemChat.query.filter_by(denuncia_id=d.id).order_by(MensagemChat.data_hora.asc()).all()
    status_msg = None
    if request.method=='POST' and 'atualizar_status' in request.form:
        ns = request.form.get('novo_status')
        if ns in ['Recebida','Em Andamento','Finalizada']:
            d.status = ns
            db.session.commit()
            status_msg = f'Status: {ns}'
    MensagemChat.query.filter_by(denuncia_id=d.id, autor='Usuário', lida_pelo_rh=False)\
        .update({'lida_pelo_rh': True})
    db.session.commit()
    if request.method=='POST' and 'mensagem' in request.form and 'atualizar_status' not in request.form:
        texto = request.form.get('mensagem','').strip()
        file  = request.files.get('anexo')
        fname = None

        # SUPORTE AO ÁUDIO NO CHAT RH
        audio_data = request.form.get("audio_blob")
        if audio_data and audio_data.startswith("data:audio"):
            header, encoded = audio_data.split(",", 1)
            audio_bytes = base64.b64decode(encoded)
            ext = ".webm"
            fname = f"{uuid.uuid4().hex}{ext}"
            with open(os.path.join(UPLOAD_FOLDER, fname), "wb") as f:
                f.write(audio_bytes)

        if not fname and file and file.filename and allowed_file(file.filename):
            ext   = file.filename.rsplit('.',1)[1].lower()
            fname = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(UPLOAD_FOLDER, fname))
        if texto or fname:
            nm = MensagemChat(denuncia_id=d.id, autor='RH', texto=texto, anexo=fname, lida_pelo_rh=True)
            db.session.add(nm)
            db.session.commit()
        return redirect(url_for('admin_denuncia', protocolo=protocolo))
    return render_template('admin_chat.html', denuncia=d, mensagens=msgs, status_msg=status_msg)

if __name__ == '__main__':
    app.run(debug=True)
