import os
from flask import Flask, redirect, url_for, session, request, render_template, flash
from authlib.integrations.flask_client import OAuth
from flask_sqlalchemy import SQLAlchemy
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', 'segredosuperseguro@123')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# OAuth Google config (usar envs)
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

class Denuncia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    texto = db.Column(db.Text, nullable=False)
    data_hora = db.Column(db.DateTime, server_default=db.func.now())
    # Email NÃO é salvo para manter anonimato!

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        if not user or not user['email'].endswith('@biocamp.com.br'):
            flash('Acesso restrito ao domínio @biocamp.com.br.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
@login_required
def index():
    return redirect(url_for('denuncia'))

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/login/google')
def login_google():
    redirect_uri = url_for('authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize')
def authorize():
    token = google.authorize_access_token()
    user_info = token['userinfo']
    email = user_info['email']
    if not email.endswith('@biocamp.com.br'):
        flash('Acesso restrito apenas para usuários autorizados.')
        return redirect(url_for('login'))
    session['user'] = user_info
    return redirect(url_for('denuncia'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/denuncia', methods=['GET', 'POST'])
@login_required
def denuncia():
    if request.method == 'POST':
        texto = request.form['texto']
        denuncia = Denuncia(texto=texto)
        db.session.add(denuncia)
        db.session.commit()
        flash('Denúncia enviada com sucesso!')
        return redirect(url_for('denuncia'))
    return render_template('denuncia.html')

@app.route('/admin')
@login_required
def admin():
    user = session.get('user')
    if user['email'] != os.environ.get('ADMIN_EMAIL'):
        return redirect(url_for('login'))
    denuncias = Denuncia.query.order_by(Denuncia.data_hora.desc()).all()
    return render_template('admin.html', denuncias=denuncias)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
