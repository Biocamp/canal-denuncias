import os
import uuid
import secrets
from flask import Flask, redirect, url_for, session, request, render_template, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message as MailMessage
from functools import wraps
from threading import Thread
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ.get('FLASK_SECRET', 'segredosuperseguro@123')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

ALLOWED_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'xls', 'xlsx',
    'mp3', 'wav', 'ogg', 'mp4', 'webm', 'mov'
}

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

class MensagemChat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    denuncia_id = db.Column(db.Integer, db.ForeignKey('denuncia.id'), nullable=False)
    autor = db.Column(db.String(30), nullable=False)  # "Usuário" ou "RH"
    texto = db.Column(db.Text, nullable=True)
    data_hora = db.Column(db.DateTime, server_default=db.func.now())
    anexo = db.Column(db.String(120), nullable=True)
    lida_pelo_rh = db.Column(db.Boolean, default=False)
    denuncia = db.relationship('Denuncia', backref=db.backref('mensagens', lazy=True))

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
    msg = MailMessage(
        subject='Nova denúncia recebida',
        sender=app.config['MAIL_USERNAME'],
        recipients=[rh_email],
        body=f"Uma nova denúncia foi registrada:\n\nProtocolo: {protocolo}\n\n{texto_denuncia}"
    )
    Thread(target=_send_async_email, args=(app, msg)).start()

# --- Decorators de permissão ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        rh_email = os.environ.get('RH_EMAIL', '').lower()
        admin_email = os.environ.get('ADMIN_EMAIL', '').lower()
        if not user or user['email'].lower() not in EMAILS_AUTORIZADOS and user['email'].lower() not in (rh_email, admin_email):
            flash('Acesso restrito apenas para usuários autorizados.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_pin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        rh_email = os.environ.get('RH_EMAIL', '').lower()
        admin_email = os.environ.get('ADMIN_EMAIL', '').lower()
        if user and user['email'].lower() in (rh_email, admin_email):
            if session.get('pending_pin') or not session.get('admin_verified'):
                return redirect(url_for('admin_verificacao'))
            return f(*args, **kwargs)
        else:
            flash('Acesso restrito ao RH.', 'danger')
            return redirect(url_for('login'))
    return decorated_function

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Verificação do PIN ADMIN ---
@app.route('/admin_verificacao', methods=['GET', 'POST'])
@login_required
def admin_verificacao():
    user = session.get('user')
    rh_email = os.environ.get('RH_EMAIL', '').lower()
    admin_email = os.environ.get('ADMIN_EMAIL', '').lower()
    if not user or user['email'].lower() not in (rh_email, admin_email):
        flash('Acesso restrito ao RH.', 'danger')
        return redirect(url_for('login'))

    if request.method == 'POST':
        pin_digitado = request.form.get('pin', '')
        pin_correto = os.environ.get('ADMIN_PIN', '123456')
        if pin_digitado == pin_correto:
            session.pop('pending_pin', None)
            session['admin_verified'] = True
            return redirect(url_for('admin'))
        else:
            flash('PIN incorreto.', 'danger')
    return render_template('admin_verificacao.html')

# --- Rotas Gerais ---
@app.route('/')
@login_required
def index():
    return redirect(url_for('denuncia'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].lower()
        rh_email = os.environ.get('RH_EMAIL', '').lower()
        admin_email = os.environ.get('ADMIN_EMAIL', '').lower()
        # PRINTS PARA DEBUG
        print("EMAIL DIGITADO:", email)
        print("RH_EMAIL do env:", rh_email)
        print("ADMIN_EMAIL do env:", admin_email)
        # ---
        if (
            email in EMAILS_AUTORIZADOS
            or email == rh_email
            or email == admin_email
        ):
            session['user'] = {'email': email}
            # se for RH ou Admin, exige o PIN!
            if email == rh_email or email == admin_email:
                session['pending_pin'] = True
                session.pop('admin_verified', None)
                return redirect(url_for('admin_verificacao'))
            else:
                return redirect(url_for('denuncia'))
        else:
            flash('E-mail não autorizado.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('pending_pin', None)
    session.pop('admin_verified', None)
    return redirect(url_for('login'))

@app.route('/denuncia', methods=['GET', 'POST'])
@login_required
def denuncia():
    if request.method == 'POST':
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
        if anexo_nome:
            msg = MensagemChat(
                denuncia_id=nova_denuncia.id,
                autor="Usuário",
                texto="Anexo inicial da denúncia.",
                anexo=anexo_nome,
                lida_pelo_rh=False
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

@app.route('/chat_arquivo/<filename>')
def chat_arquivo(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# --- Consulta/Chat Usuário ---
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
    return render_template('consulta.html', denuncia=denuncia, mensagens=mensagens, protocolo=protocolo)

@app.route('/chat/<protocolo>', methods=['POST'])
def chat(protocolo):
    denuncia = Denuncia.query.filter_by(protocolo=protocolo).first()
    if not denuncia:
        flash("Protocolo não encontrado.", "danger")
        return redirect(url_for('consulta'))

    # Não deixa enviar se finalizada
    if denuncia.status == 'Finalizada':
        flash("Essa conversa foi encerrada e não pode mais receber mensagens.", "danger")
        return redirect(url_for('consulta', protocolo=protocolo))

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
            anexo=anexo_nome,
            lida_pelo_rh=False
        )
        db.session.add(msg)
        db.session.commit()
    return redirect(url_for('consulta', protocolo=protocolo))

# --- Painel ADMIN/RH ---
@app.route('/admin', methods=['GET'])
@login_required
@admin_pin_required
def admin():
    user = session.get('user')
    rh_email = os.environ.get('RH_EMAIL', '').lower()
    admin_email = os.environ.get('ADMIN_EMAIL', '').lower()
    if not user or user['email'].lower() not in (rh_email, admin_email):
        flash('Acesso restrito ao RH.', 'danger')
        return redirect(url_for('login'))

    # Para badge de novas mensagens: conta quantas mensagens não lidas pelo RH existem em cada denúncia
    subq = db.session.query(
        MensagemChat.denuncia_id.label('denuncia_id'),
        db.func.count().label('novas_msgs')
    ).filter(
        MensagemChat.autor == "Usuário",
        MensagemChat.lida_pelo_rh == False
    ).group_by(MensagemChat.denuncia_id).subquery()

    rows = db.session.query(
        Denuncia,
        subq.c.novas_msgs
    ).outerjoin(subq, Denuncia.id == subq.c.denuncia_id
    ).order_by(Denuncia.data_hora.desc()).all()

    denuncias = [row[0] for row in rows]
    unread_counts = {row[0].protocolo: int(row[1] or 0) for row in rows}

    return render_template('admin.html', denuncias=denuncias, unread_counts=unread_counts)

@app.route('/admin/denuncia/<protocolo>', methods=['GET', 'POST'])
@login_required
@admin_pin_required
def admin_denuncia(protocolo):
    user = session.get('user')
    rh_email = os.environ.get('RH_EMAIL', '').lower()
    admin_email = os.environ.get('ADMIN_EMAIL', '').lower()
    if not user or user['email'].lower() not in (rh_email, admin_email):
        flash('Acesso restrito ao RH.', 'danger')
        return redirect(url_for('login'))

    denuncia = Denuncia.query.filter_by(protocolo=protocolo).first_or_404()
    mensagens = MensagemChat.query.filter_by(denuncia_id=denuncia.id).order_by(MensagemChat.data_hora.asc()).all()

    # Atualizar status da denúncia
    status_msg = None
    if request.method == 'POST' and 'atualizar_status' in request.form:
        novo_status = request.form.get('novo_status')
        if novo_status and novo_status in ['Recebida', 'Em Andamento', 'Finalizada']:
            denuncia.status = novo_status
            db.session.commit()
            status_msg = f"Status atualizado para: {novo_status}"

    # Marcar mensagens do usuário como lidas quando o RH entra no chat
    MensagemChat.query.filter_by(denuncia_id=denuncia.id, autor='Usuário', lida_pelo_rh=False).update({'lida_pelo_rh': True})
    db.session.commit()

    # Enviar mensagem do RH
    if request.method == 'POST' and 'mensagem' in request.form and 'atualizar_status' not in request.form:
        texto = request.form.get('mensagem', '').strip()
        file = request.files.get('anexo')
        filename = None
        if file and file.filename and allowed_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(UPLOAD_FOLDER, filename))
        if texto or filename:
            nova_msg = MensagemChat(
                denuncia_id=denuncia.id,
                autor='RH',
                texto=texto,
                anexo=filename,
                lida_pelo_rh=True
            )
            db.session.add(nova_msg)
            db.session.commit()
        return redirect(url_for('admin_denuncia', protocolo=protocolo))

    return render_template(
        'admin_chat.html',
        denuncia=denuncia,
        mensagens=mensagens,
        status_msg=status_msg
    )

if __name__ == "__main__":
    app.run(debug=True)
