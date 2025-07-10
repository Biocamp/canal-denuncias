# ... (cabeçalho igual ao seu até a definição dos modelos)

class MensagemChat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    denuncia_id = db.Column(db.Integer, db.ForeignKey('denuncia.id'), nullable=False)
    autor = db.Column(db.String(30), nullable=False)  # "Usuário" ou "RH"
    texto = db.Column(db.Text, nullable=True)
    data_hora = db.Column(db.DateTime, server_default=db.func.now())
    anexo = db.Column(db.String(120), nullable=True)  # nome do arquivo salvo
    lida_pelo_rh = db.Column(db.Boolean, default=False)   # NOVO CAMPO!
    denuncia = db.relationship('Denuncia', backref=db.backref('mensagens', lazy=True))

with app.app_context():
    db.create_all()

# ... (demais funções/métodos iguais)

@app.route('/admin', methods=['GET'])
@login_required
def admin():
    user = session.get('user')
    rh_email = os.environ.get('RH_EMAIL', '').lower()
    admin_email = os.environ.get('ADMIN_EMAIL', '').lower()
    if not user or user['email'].lower() not in (rh_email, admin_email):
        flash('Acesso restrito ao RH.', 'danger')
        return redirect(url_for('login'))

    denuncias = Denuncia.query.order_by(Denuncia.data_hora.desc()).all()

    # Para cada denúncia, conte mensagens NÃO lidas pelo RH e que são do usuário
    unread_counts = {}
    for d in denuncias:
        unread = MensagemChat.query.filter_by(denuncia_id=d.id, autor='Usuário', lida_pelo_rh=False).count()
        unread_counts[d.protocolo] = unread

    return render_template('adminrh.html', denuncias=denuncias, unread_counts=unread_counts)

@app.route('/admin/denuncia/<protocolo>', methods=['GET', 'POST'])
@login_required
def admin_denuncia(protocolo):
    user = session.get('user')
    rh_email = os.environ.get('RH_EMAIL', '').lower()
    admin_email = os.environ.get('ADMIN_EMAIL', '').lower()
    if not user or user['email'].lower() not in (rh_email, admin_email):
        flash('Acesso restrito ao RH.', 'danger')
        return redirect(url_for('login'))

    denuncia = Denuncia.query.filter_by(protocolo=protocolo).first_or_404()
    mensagens = MensagemChat.query.filter_by(denuncia_id=denuncia.id).order_by(MensagemChat.data_hora.asc()).all()

    # Marcar como lidas todas mensagens do usuário nesta denúncia
    MensagemChat.query.filter_by(denuncia_id=denuncia.id, autor='Usuário', lida_pelo_rh=False).update({'lida_pelo_rh': True})
    db.session.commit()

    if request.method == 'POST':
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
                lida_pelo_rh=True  # Mensagens do RH já são "lidas"
            )
            db.session.add(nova_msg)
            db.session.commit()
        return redirect(url_for('admin_denuncia', protocolo=protocolo))

    return render_template('admin_chat.html', denuncia=denuncia, mensagens=mensagens)
# ... (demais rotas igual ao seu original)
