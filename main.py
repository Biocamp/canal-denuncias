import os
import uuid
from flask import Flask, redirect, url_for, session, request, render_template, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from functools import wraps
from threading import Thread
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
import secrets

app = Flask(__name__)
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ.get('FLASK_SECRET', 'segredosuperseguro@123')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'xls', 'xlsx'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Configuração Flask-Mail via ENV ---
app.config.update(
    MAIL_SERVER=os.environ.get('MAIL_SERVER'),
    MAIL_PORT=int(os.environ.get('MAIL_PORT', 465)),
    MAIL_USE_SSL=True,
    MAIL_USERNAME=os.environ.get('MAIL_USERNAME'),
    MAIL_PASSWORD=os.environ.get('MAIL_PASSWORD'),
)
mail = Mail(app)
db = SQLAlchemy(app)

# --- Modelos ---
class Denuncia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    texto = db.Column(db.Text, nullable=False)
    data_hora = db.Column(db.DateTime, server_default=db.func.now())
    protocolo = db.Column(db.String(20), unique=True, nullable=False)
    status = db.Column(db.String(30), default='Recebida')
    observacao = db.Column(db.Text, nullable=True)
    mensagens = db.relationship('MensagemChat', backref='denuncia', lazy=True)

class MensagemChat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    denuncia_id = db.Column(db.Integer, db.ForeignKey('denuncia.id'), nullable=False)
    autor = db.Column(db.String(30), nullable=False)  # "Usuário" ou "RH"
    texto = db.Column(db.Text, nullable=True)
    data_hora = db.Column(db.DateTime, server_default=db.func.now())
    anexo = db.Column(db.String(120), nullable=True)  # nome do arquivo salvo

with app.app_context():
    db.create_all()

# --- Autorizados ---
def carregar_emails_autorizados(arquivo='autorizados.txt'):
    if not os.path.exists(arquivo):
        return []
    with open(arquivo, 'r', encoding='utf-8') as f:
        return [linha.strip().lower() for linha in f if linha.strip()]

EMAILS_AUTORIZADOS = carregar_emails_autorizados()

# --- Envio de e-mail ao RH ---
def _send_async_email(app, msg):
    with app.app_context():
        mail.send(msg)

def notify_rh(texto_denuncia, protocolo):
    rh_email = os.environ.get('RH_EMAIL')
    if not rh_email:
        return
    msg = Message(
        subject='Nova denúncia recebida',
        sender=app.config['MAIL_USERNAME'],
        recipients=[rh_email],
        body=f"Uma nova denúncia foi registrada:\n\nProtocolo: {protocolo}\n\n{texto_denuncia}"
    )
    Thread(target=_send_async_email, args=(app, msg)).start()

# --- Decorator de acesso ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        if not user or user['email'].lower() not in EMAILS_AUTORIZADOS:
            flash('Acesso restrito apenas para usuários autorizados.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Funções auxiliares para arquivos ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Rotas ---
@app.route('/')
@login_required
def index():
    return redirect(url_for('denuncia'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].lower()
        if email in EMAILS_AUTORIZADOS:
            session['user'] = {'email': email}
            return redirect(url_for('denuncia'))
        else:
            flash('E-mail não autorizado.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/denuncia', methods=['GET', 'POST'])
@login_required
def denuncia():
    if request.method == 'POST':
        # validação do checkbox
        if not request.form.get('terms'):
            flash('Você precisa aceitar os termos e condições para prosseguir.', 'warning')
            return redirect(url_for('denuncia'))

        texto = request.form['texto']
        file = request.files.get('anexo')
        anexo_nome = None

        if file and file.filename and allowed_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            anexo_nome = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(UPLOAD_FOLDER, anexo_nome))

        protocolo = secrets.token_hex(6).upper()
        nova_denuncia = Denuncia(
            texto=texto,
            protocolo=protocolo,
            status='Recebida',
            observacao=None
        )
        db.session.add(nova_denuncia)
        db.session.commit()
        # Se houver anexo na denúncia, salva como mensagem de chat inicial
        if anexo_nome:
            msg = MensagemChat(
                denuncia_id=nova_denuncia.id,
                autor="Usuário",
                texto="Anexo inicial da denúncia.",
                anexo=anexo_nome
            )
            db.session.add(msg)
            db.session.commit()
        notify_rh(texto, protocolo)
        flash(f'Denúncia enviada com sucesso! Salve seu protocolo: {protocolo}', 'success')
        return redirect(url_for('denuncia'))

    return render_template('denuncia.html')

@app.route('/termos')
@login_required
def termos():
    return render_template('termos.html')

@app.route('/admin')
@login_required
def admin():
    user = session.get('user')
    if user['email'].lower() != os.environ.get('ADMIN_EMAIL', '').lower():
        return redirect(url_for('login'))
    denuncias = Denuncia.query.order_by(Denuncia.data_hora.desc()).all()
    return render_template('admin.html', denuncias=denuncias)

@app.route('/consulta', methods=['GET', 'POST'])
def consulta():
    denuncia = None
    mensagens = []
    protocolo = None
    if request.method == 'POST':
        protocolo = request.form.get('protocolo', '').strip().upper()
    elif request.method == 'GET' and 'protocolo' in request.args:
        protocolo = request.args.get('protocolo', '').strip().upper()

    if protocolo:
        denuncia = Denuncia.query.filter_by(protocolo=protocolo).first()
        if not denuncia:
            flash("Nenhuma denúncia encontrada para esse protocolo.", "warning")
        else:
            mensagens = MensagemChat.query.filter_by(denuncia_id=denuncia.id).order_by(MensagemChat.data_hora.asc()).all()
    return render_template('consulta.html', denuncia=denuncia, mensagens=mensagens)

@app.route('/chat/<protocolo>', methods=['POST'])
def chat(protocolo):
    denuncia = Denuncia.query.filter_by(protocolo=protocolo).first()
    if not denuncia:
        flash("Protocolo não encontrado.", "danger")
        return redirect(url_for('consulta'))

    texto = request.form.get('mensagem', '').strip()
    file = request.files.get('anexo')
    anexo_nome = None

    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        anexo_nome = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(UPLOAD_FOLDER, anexo_nome))

    if texto or anexo_nome:
        msg = MensagemChat(
            denuncia_id=denuncia.id,
            autor="Usuário",
            texto=texto if texto else None,
            anexo=anexo_nome
        )
        db.session.add(msg)
        db.session.commit()

    # Fixar protocolo para manter o chat aberto após envio
    return redirect(url_for('consulta', protocolo=protocolo))

@app.route('/chat_arquivo/<filename>')
def chat_arquivo(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == "__main__":
    app.run(debug=True)
