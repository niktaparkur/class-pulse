from flask import render_template, request, redirect, url_for, flash, current_app
from flask_login import (
    login_user,
    logout_user,
    login_required,
    current_user as flask_login_current_user,
)
from . import superadmin_bp, sa_login_manager  # Импортируем свой login_manager
from ..models import db, SuperAdmin, School, Director  # Нужные модели (из meta_db)
from werkzeug.security import check_password_hash  # Если не используем методы модели


@sa_login_manager.user_loader
def load_superadmin(user_id):
    # current_app.logger.debug(f"--- superadmin_loader: Loading SA with ID: {user_id} ---")
    return db.session.get(SuperAdmin, int(user_id))


@superadmin_bp.route("/login", methods=["GET", "POST"])
def login_route():
    # current_app.logger.debug(f"Accessing SA login. Current user auth: {flask_login_current_user.is_authenticated}, type: {type(flask_login_current_user)}")
    if flask_login_current_user.is_authenticated and isinstance(
        flask_login_current_user, SuperAdmin
    ):
        return redirect(url_for("superadmin.dashboard"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        sa_user = db.session.execute(
            db.select(SuperAdmin).filter_by(username=username)
        ).scalar_one_or_none()

        if sa_user and sa_user.check_password(password):
            login_user(
                sa_user
            )  # Эта функция использует тот LoginManager, который активен для текущего запроса (или глобальный)
            # Убедись, что sa_login_manager корректно инициализирован для app
            flash("Вход Суперадмина выполнен успешно!", "success")
            return redirect(url_for("superadmin.dashboard"))
        else:
            flash("Неверное имя пользователя или пароль Суперадмина.", "danger")
    return render_template(
        "sa_login.html"
    )  # Нужен шаблон app/superadmin/templates/sa_login.html


@superadmin_bp.route("/logout")
@login_required  # Этот декоратор будет использовать sa_login_manager
def logout_route():
    if not isinstance(flask_login_current_user, SuperAdmin):  # Доп. проверка
        flash("Действие не разрешено.", "danger")
        return redirect(url_for("core.teacher_dashboard"))  # Или на общий вход

    username = flask_login_current_user.username
    logout_user()
    flash(f"Суперадмин {username} успешно вышел.", "info")
    return redirect(url_for("superadmin.login_route"))


@superadmin_bp.route("/")  # /superadmin/
@superadmin_bp.route("/dashboard")  # /superadmin/dashboard
@login_required
def dashboard():
    if not isinstance(flask_login_current_user, SuperAdmin):
        flash("Доступ к панели Суперадмина запрещен.", "danger")
        # Редирект на соответствующую страницу входа или главную, если основной login_manager конфликтует
        return redirect(url_for("superadmin.login_route"))

    schools_list = (
        db.session.execute(db.select(School).order_by(School.name)).scalars().all()
    )
    return render_template(
        "sa_dashboard.html", schools_list=schools_list
    )  # Нужен шаблон


# Тут будут маршруты для создания школ, назначения директоров...
# Например, @superadmin_bp.route('/schools/new', methods=['GET', 'POST'])
# def create_school(): ...
