from flask import render_template, request, redirect, url_for, flash, current_app, abort
from flask_login import login_user, logout_user, login_required
from flask_login import (
    current_user as flask_login_current_user,
)  # Переименовываем, чтобы не путать с логикой

from . import director_bp, dir_login_manager
from ..models import db, Director, Teacher, School  # Модели из meta_db и app_db

# from werkzeug.security import generate_password_hash # Если нужно создавать учителей с паролем напрямую



def load_director(user_id):
    # current_app.logger.debug(f"--- director_loader: Loading Director with ID: {user_id} ---")
    return db.session.get(Director, int(user_id))


@director_bp.route("/login", methods=["GET", "POST"])
def login_route():
    if flask_login_current_user.is_authenticated and isinstance(
        flask_login_current_user, Director
    ):
        return redirect(url_for("director.dashboard"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        # Директора ищутся в meta_db
        dir_user = db.session.execute(
            db.select(Director).filter_by(username=username)
        ).scalar_one_or_none()

        if dir_user and dir_user.check_password(password):
            login_user(dir_user)
            flash(
                f"Вход Директора {dir_user.username} (школа: {dir_user.school.name}) выполнен успешно!",
                "success",
            )
            return redirect(url_for("director.dashboard"))
        else:
            flash("Неверное имя пользователя или пароль Директора.", "danger")
    return render_template(
        "dir_login.html"
    )  # Нужен шаблон app/director/templates/dir_login.html


@director_bp.route("/logout")
@login_required
def logout_route():
    if not isinstance(flask_login_current_user, Director):
        flash("Действие не разрешено.", "danger")
        return redirect(url_for("core.teacher_dashboard"))  # Или на главный вход

    username = flask_login_current_user.username
    logout_user()
    flash(f"Директор {username} успешно вышел.", "info")
    return redirect(url_for("director.login_route"))


@director_bp.route("/")
@director_bp.route("/dashboard")
@login_required
def dashboard():
    if not isinstance(flask_login_current_user, Director):
        abort(403)  # Запрещено!

    director = flask_login_current_user  # Теперь это точно Директор
    # Учителя из app_db, но фильтруем по school_id директора
    teachers_list = (
        db.session.execute(
            db.select(Teacher)
            .filter_by(school_id=director.school_id)
            .order_by(Teacher.username)
        )
        .scalars()
        .all()
    )

    return render_template(
        "dir_dashboard.html",
        school_name=director.school.name,
        teachers_list=teachers_list,
    )


@director_bp.route("/teachers/new", methods=["GET", "POST"])
@login_required
def create_teacher():
    if not isinstance(flask_login_current_user, Director):
        abort(403)
    director = flask_login_current_user

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get(
            "password", ""
        ).strip()  # Пароль генерируем или даем задать

        if not username or not password:
            flash("Имя пользователя и пароль обязательны.", "warning")
            return render_template(
                "dir_create_teacher.html", school_name=director.school.name
            )

        # Проверка на уникальность имени учителя В ПРЕДЕЛАХ ВСЕХ ШКОЛ (или только своей - решай сам)
        # Для простоты - уникальность в пределах всех учителей (app_db)
        existing_teacher = db.session.execute(
            db.select(Teacher).filter_by(username=username)
        ).scalar_one_or_none()
        if existing_teacher:
            flash(
                f'Учитель с именем пользователя "{username}" уже существует.', "error"
            )
            return render_template(
                "dir_create_teacher.html", school_name=director.school.name
            )

        try:
            new_teacher = Teacher(username=username, school_id=director.school_id)
            new_teacher.set_password(password)  # Метод из модели Teacher (UserMixin)
            db.session.add(new_teacher)
            db.session.commit()
            flash(
                f'Учитель "{username}" успешно создан для школы "{director.school.name}".',
                "success",
            )
            current_app.logger.info(
                f"Director {director.username} created teacher {username} for school ID {director.school_id}"
            )
            return redirect(url_for("director.dashboard"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error creating teacher by director {director.username}: {e}"
            )
            flash("Ошибка при создании учителя.", "error")

    return render_template(
        "dir_create_teacher.html", school_name=director.school.name
    )  # Нужен шаблон


# Тут же будут маршруты для редактирования/удаления учителей (помни про school_id)
