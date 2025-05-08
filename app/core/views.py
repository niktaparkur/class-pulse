# app/core/views.py
import os
import uuid
import csv
import io
import json

# from urllib.parse import urljoin # urljoin здесь не нужен, он в utils
from collections import Counter

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    send_from_directory,
    Response,
    make_response,
    abort,
    flash,
    session,
    current_app,
)
from markupsafe import Markup
from flask_login import login_user, logout_user, login_required, current_user
from flask_socketio import emit, join_room, leave_room

# werkzeug.security импортируется в models для User, здесь не обязателен, если не создаем юзера напрямую

from . import core_bp
from ..models import db, Teacher, Poll, PollTemplate, SuperAdmin, School, Director, User
from .. import socketio, login_manager
from ..utils import (
    is_safe_url,
    get_base_url_for_links,
    DEFAULT_PULSE_QUESTION,
    DEFAULT_PULSE_OPTIONS,
    DEFAULT_FEEDBACK_QUESTIONS,
    QR_CODES_FOLDER_NAME,
    check_profanity_library,
)

import qrcode

client_ips = {}


# --- Маршруты Flask ---
@core_bp.route("/login", methods=["GET", "POST"])
def login_route():
    current_app.logger.debug(f"Accessing /login route (Blueprint: {core_bp.name})")
    if current_user.is_authenticated:
        current_app.logger.debug(
            "User already authenticated, redirecting to dashboard."
        )
        return redirect(url_for("core.teacher_dashboard"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        remember = bool(request.form.get("remember"))
        current_app.logger.info(f"Login attempt for user: {username}")

        user = db.session.execute(
            db.select(User).filter_by(username=username)
        ).scalar_one_or_none()

        if user and user.check_password(password):
            current_app.logger.info(f"Password for {username} is correct.")
            try:
                login_user(user, remember=remember)
                user_id_in_session = session.get("_user_id")
                is_current_user_auth = current_user.is_authenticated
                current_app.logger.info(
                    f"User {username} logged in. Session['_user_id']={user_id_in_session}. "
                    f"current_user.is_authenticated={is_current_user_auth}"
                )
                flash("Вход выполнен успешно!", "success")
                next_page_arg = request.args.get("next")
                final_redirect_url = url_for("core.teacher_dashboard")  # По умолчанию
                if next_page_arg and is_safe_url(next_page_arg):
                    final_redirect_url = next_page_arg
                elif next_page_arg:
                    current_app.logger.debug(
                        f"Login: 'next' URL '{next_page_arg}' was unsafe. Redirecting to dashboard."
                    )
                return redirect(final_redirect_url)
            except Exception as e:
                current_app.logger.error(
                    f"Error during login_user or redirect for user {username}: {e}"
                )
                flash("Произошла внутренняя ошибка при входе.", "error")
                return redirect(url_for("core.login_route"))
        else:
            current_app.logger.warning(f"Failed login attempt for user: {username}")
            flash("Неверное имя пользователя или пароль.", "error")
    return render_template("login.html")


@core_bp.route("/logout")
@login_required
def logout_route():
    username = current_user.username
    logout_user()
    flash("Вы успешно вышли из системы.", "info")
    current_app.logger.info(f"User {username} logged out.")
    return redirect(url_for("core.login_route"))


@core_bp.route("/")
@core_bp.route("/teacher")
@login_required
def teacher_dashboard():
    current_app.logger.debug(
        f"Accessing teacher_dashboard for user {current_user.username}"
    )
    try:
        current_teacher = flask_login_current_user
        active_polls_list = (
            db.session.execute(
                db.select(Poll)
                .filter_by(is_active=True, teacher_id=current_teacher.id)
                .order_by(Poll.created_at.desc())
            )
            .scalars()
            .all()
        )
        completed_polls_list = (
            db.session.execute(
                db.select(Poll)
                .filter_by(is_active=False)
                .order_by(Poll.created_at.desc())
            )
            .scalars()
            .all()
        )
        all_templates = (
            db.session.execute(db.select(PollTemplate).order_by(PollTemplate.name))
            .scalars()
            .all()
        )
        pulse_templates = [t for t in all_templates if t.poll_type == "pulse"]
        feedback_templates = [t for t in all_templates if t.poll_type == "feedback"]
        current_app.logger.debug(
            f"Fetched {len(active_polls_list)} active, {len(completed_polls_list)} completed polls "
            f"and {len(all_templates)} templates."
        )
    except Exception as e:
        current_app.logger.error(f"Error in teacher_dashboard data loading: {e}")
        db.session.rollback()
        flash("Произошла ошибка при загрузке данных панели.", "error")
        active_polls_list, completed_polls_list, pulse_templates, feedback_templates = (
            [],
            [],
            [],
            [],
        )

    return render_template(
        "teacher.html",
        active_polls_list=active_polls_list,
        completed_polls_list=completed_polls_list,
        pulse_templates=pulse_templates,
        feedback_templates=feedback_templates,
        DEFAULT_PULSE_QUESTION=DEFAULT_PULSE_QUESTION,
        DEFAULT_PULSE_OPTIONS=DEFAULT_PULSE_OPTIONS,
        DEFAULT_FEEDBACK_QUESTIONS=DEFAULT_FEEDBACK_QUESTIONS,
    )


@core_bp.route("/start/<poll_type>", methods=["POST"])
@login_required
def start_poll(poll_type):
    if poll_type not in ["pulse", "feedback"]:
        abort(400, "Invalid poll type specified.")

    session_id = str(uuid.uuid4())[:8]
    current_app.logger.info(
        f"Starting new poll creation: type={poll_type}, generated_id={session_id}"
    )

    template_id_str = request.form.get("template_id")
    custom_pulse_question = request.form.get("custom_question", "").strip()
    custom_feedback_question_text = request.form.get(
        "custom_feedback_question", ""
    ).strip()

    poll_question = None
    poll_options = None
    poll_questions_list = None
    source_info = ""
    selected_template = None

    if template_id_str:
        try:
            template_id = int(template_id_str)
            selected_template = db.session.get(PollTemplate, template_id)
            if selected_template and selected_template.poll_type == poll_type:
                template_data = selected_template.get_data()
                if not template_data:
                    flash(
                        f"Не удалось загрузить данные из шаблона '{selected_template.name}'.",
                        "error",
                    )
                    return redirect(url_for("core.teacher_dashboard"))

                source_info = f"по шаблону '{selected_template.name}'"
                if poll_type == "pulse":
                    poll_question = template_data.get("question")
                    poll_options = template_data.get("options")
                    if not isinstance(poll_question, str) or not isinstance(
                        poll_options, list
                    ):
                        flash(
                            f"Ошибка в данных шаблона Пульс '{selected_template.name}'.",
                            "error",
                        )
                        return redirect(url_for("core.teacher_dashboard"))
                elif poll_type == "feedback":
                    if isinstance(template_data, list):
                        poll_questions_list = template_data
                        for q_idx, q_item in enumerate(poll_questions_list):
                            if not isinstance(q_item, dict) or not all(
                                k in q_item for k in ("id", "text", "type")
                            ):
                                flash(
                                    f"Ошибка в структуре вопроса #{q_idx+1} шаблона ОС '{selected_template.name}'. Отсутствуют ключи id, text или type.",
                                    "error",
                                )
                                return redirect(url_for("core.teacher_dashboard"))
                    else:
                        flash(
                            f"Ошибка формата данных шаблона ОС '{selected_template.name}'. Ожидался список вопросов.",
                            "error",
                        )
                        return redirect(url_for("core.teacher_dashboard"))
                current_app.logger.info(
                    f"Using template '{selected_template.name}' for poll {session_id}"
                )
            elif selected_template:
                flash(
                    f"Шаблон '{selected_template.name}' не подходит для типа опроса '{poll_type}'.",
                    "warning",
                )
                selected_template = None
            else:
                flash(f"Шаблон с ID {template_id} не найден.", "warning")
                selected_template = None
        except (ValueError, TypeError) as e:
            current_app.logger.warning(f"Invalid template_id '{template_id_str}': {e}")
            flash("Неверный ID шаблона.", "error")
            selected_template = None

    if not selected_template:
        if poll_type == "pulse":
            poll_question = (
                custom_pulse_question
                if custom_pulse_question
                else DEFAULT_PULSE_QUESTION
            )
            poll_options = DEFAULT_PULSE_OPTIONS
            source_info = (
                "со своим вопросом"
                if custom_pulse_question
                else "по стандартным настройкам"
            )
            current_app.logger.info(
                f"Using {'custom' if custom_pulse_question else 'default'} pulse question for poll {session_id}"
            )
        elif poll_type == "feedback":
            if custom_feedback_question_text:
                poll_questions_list = [
                    {
                        "id": f"custom_q_{uuid.uuid4().hex[:4]}",
                        "text": custom_feedback_question_text,
                        "type": "text",
                    }
                ]
                source_info = "со своим вопросом"
                current_app.logger.info(
                    f"Using custom feedback question for poll {session_id}"
                )
            else:
                poll_questions_list = DEFAULT_FEEDBACK_QUESTIONS
                source_info = "по стандартным настройкам"
                current_app.logger.info(
                    f"Using default feedback questions for poll {session_id}"
                )

    if (poll_type == "pulse" and (not poll_question or not poll_options)) or (
        poll_type == "feedback" and not poll_questions_list
    ):
        flash("Критическая ошибка: Не удалось определить данные для опроса.", "error")
        current_app.logger.error(
            f"Failed to set poll data before creating poll {session_id}. Type={poll_type}, "
            f"Q={poll_question}, Opts={poll_options}, QList={poll_questions_list}"
        )
        return redirect(url_for("core.teacher_dashboard"))

    base_url = get_base_url_for_links()
    poll_path = url_for(
        "core.student_poll_page", session_id=session_id, _external=False
    )
    student_url = f"{base_url.rstrip('/')}{poll_path}"
    current_app.logger.info(f"Final student_url for poll {session_id}: {student_url}")

    new_poll = Poll(id=session_id, poll_type=poll_type, student_url=student_url)
    initial_results = {}
    if poll_type == "pulse":
        new_poll.pulse_question = poll_question
        new_poll.set_options(poll_options)
        initial_results = {option: 0 for option in poll_options}
    elif poll_type == "feedback":
        new_poll.set_questions(poll_questions_list)
        for q in poll_questions_list:
            if (
                q.get("type") == "scale"
                and q.get("id")
                and isinstance(q.get("options"), list)
            ):
                initial_results[q.get("id")] = {option: 0 for option in q["options"]}
        new_poll.text_feedback_responses = json.dumps([])
    new_poll.aggregated_results = json.dumps(initial_results, ensure_ascii=False)

    blueprint_static_folder = current_app.blueprints["core"].static_folder
    if not blueprint_static_folder:
        current_app.logger.error(f"Static folder for blueprint 'core' not found!")
        core_bp_root_path = current_app.blueprints["core"].root_path
        blueprint_static_folder = os.path.join(core_bp_root_path, "static")

    qr_folder_path = os.path.join(blueprint_static_folder, QR_CODES_FOLDER_NAME)

    if not os.path.exists(qr_folder_path):
        try:
            os.makedirs(qr_folder_path)
            current_app.logger.info(f"Created QR code directory: {qr_folder_path}")
        except OSError as e:
            current_app.logger.error(f"Error creating directory {qr_folder_path}: {e}")

    qr_filename = f"{session_id}.png"
    qr_filepath = os.path.join(qr_folder_path, qr_filename)
    new_poll.qr_code_url = None
    try:
        current_app.logger.info(
            f"Generating QR code for URL: {student_url} to path: {qr_filepath}"
        )
        qr_img = qrcode.make(student_url)
        qr_img.save(qr_filepath)
        new_poll.qr_code_url = url_for(
            "core.static", filename=f"{QR_CODES_FOLDER_NAME}/{qr_filename}"
        )
        current_app.logger.info(f"QR code URL set to: {new_poll.qr_code_url}")
    except Exception as e:
        current_app.logger.error(
            f"Error generating QR code for poll {session_id} with URL {student_url}: {e}"
        )

    try:
        db.session.add(new_poll)
        db.session.commit()
        current_app.logger.info(
            f"Poll {session_id} ({poll_type}) created successfully."
        )
        flash_message_text = f"Опрос '{new_poll.pulse_question if poll_type=='pulse' else 'Обратная связь'}' ({source_info}) запущен!"
        if new_poll.student_url:
            flash_message_text += f" Ссылка: <a href='{new_poll.student_url}' target='_blank'>{new_poll.student_url}</a>"
        flash(Markup(flash_message_text), "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"ERROR saving poll {session_id} to DB: {e}")
        flash("Ошибка при создании опроса в базе данных.", "error")
        return redirect(url_for("core.teacher_dashboard"))

    return redirect(url_for("core.teacher_dashboard"))


@core_bp.route("/poll/<session_id>")
def student_poll_page(session_id):
    try:
        poll = db.session.get(Poll, session_id)
        if not poll:
            current_app.logger.warning(
                f"Student poll page: Poll {session_id} not found."
            )
            return render_template("error.html", message="Опрос не найден."), 404
        if not poll.is_active:
            current_app.logger.info(
                f"Student poll page: Poll {session_id} is not active."
            )
            return (
                render_template("error.html", message="Опрос завершен."),
                403,
            )  # 403 Forbidden
    except Exception as e:
        current_app.logger.error(
            f"Error fetching poll {session_id} for student page: {e}"
        )
        db.session.rollback()
        return (
            render_template("error.html", message="Ошибка при доступе к опросу."),
            500,
        )

    cookie_name = f"voted_{session_id}"
    if request.cookies.get(cookie_name) == "yes":
        current_app.logger.info(
            f"Student poll page: User has already voted for poll {session_id} (cookie found)."
        )
        return render_template("already_voted.html")

    if poll.poll_type == "pulse":
        return render_template(
            "student_pulse.html",
            session_id=poll.id,
            question=poll.pulse_question,
            options=poll.get_options(),
        )
    elif poll.poll_type == "feedback":
        return render_template(
            "student_feedback.html", session_id=poll.id, questions=poll.get_questions()
        )
    else:
        current_app.logger.error(
            f"Unknown poll type '{poll.poll_type}' for poll {session_id}"
        )
        return render_template("error.html", message="Неизвестный тип опроса."), 500


@core_bp.route("/submit/<session_id>", methods=["POST"])
def submit_response(session_id):
    try:
        poll = db.session.get(Poll, session_id)
        if not poll:
            current_app.logger.warning(f"Submit response: Poll {session_id} not found.")
            return render_template("error.html", message="Опрос не найден."), 404
        if not poll.is_active:
            current_app.logger.warning(
                f"Submit response: Poll {session_id} is not active."
            )
            return render_template("error.html", message="Опрос уже завершен."), 403
    except Exception as e:
        current_app.logger.error(
            f"Error fetching poll {session_id} for submission: {e}"
        )
        db.session.rollback()
        return (
            render_template("error.html", message="Ошибка при доступе к опросу."),
            500,
        )

    cookie_name = f"voted_{session_id}"
    if request.cookies.get(cookie_name) == "yes":
        current_app.logger.info(
            f"Submit response: User has already voted for poll {session_id} (cookie found)."
        )
        return render_template("already_voted.html")

    form_data = request.form
    identify = form_data.get("identify_me") == "yes"
    student_name_raw = form_data.get("student_name", "").strip()
    student_name = student_name_raw if identify and student_name_raw else None
    updated = False

    try:
        if poll.poll_type == "pulse":
            answer = form_data.get("answer")
            if answer and answer in poll.get_options():
                poll.update_aggregated_results(answer)
                updated = True
            else:
                current_app.logger.warning(
                    f"Invalid pulse answer '{answer}' received for poll {session_id}"
                )
                return (
                    render_template(
                        "error.html",
                        message="Ответ не был предоставлен или некорректен.",
                    ),
                    400,
                )
        elif poll.poll_type == "feedback":
            questions = poll.get_questions()
            q_map = {q["id"]: q for q in questions}
            for q_id_form, answer_form in form_data.items():
                if q_id_form in q_map:
                    question = q_map[q_id_form]
                    q_type = question.get("type")
                    answer_value = answer_form.strip()
                    if answer_value:
                        if q_type == "scale" and answer_value in question.get(
                            "options", []
                        ):
                            poll.update_aggregated_results(
                                answer_value, question_id=q_id_form
                            )
                            updated = True
                        elif q_type == "text":
                            is_flagged = False
                            is_flagged = check_profanity_library(answer_value)

                            if is_flagged:
                                current_app.logger.info(
                                    f"Profanity detected in submission for poll {session_id}, question ID {q_id_form}. Text: '{answer_value[:50]}...'"
                                )

                            poll.add_text_response(
                                answer_value,
                                student_name,
                                q_id_form,
                                is_flagged=is_flagged,
                            )
                            updated = True

        if updated:
            poll.response_count = Poll.response_count + 1
            db.session.add(poll)
            db.session.commit()
            current_app.logger.info(
                f"Poll {session_id} updated after submission. New count: {poll.response_count}"
            )

            try:
                payload = poll.get_results_payload()
                socketio.emit("results_updated", payload, room=session_id)
                current_app.logger.info(
                    f"SocketIO emitted 'results_updated' to room {session_id}"
                )
            except Exception as e_socket:
                current_app.logger.error(
                    f"Error emitting WebSocket update for {session_id}: {e_socket}"
                )
        else:
            current_app.logger.info(
                f"No valid updates found for poll {session_id} submission, skipping commit."
            )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"ERROR updating poll {session_id} after submission: {e}"
        )
        return (
            render_template(
                "error.html", message="Ошибка при сохранении вашего ответа."
            ),
            500,
        )

    response = make_response(render_template("thank_you.html"))
    response.set_cookie(
        cookie_name,
        "yes",
        max_age=60 * 60 * 24 * 7,
        httponly=current_app.config.get("SESSION_COOKIE_HTTPONLY", True),
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
        samesite=current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
    )
    return response


@core_bp.route("/results/<session_id>")
@login_required
def view_results(session_id):
    try:
        poll = db.session.get(Poll, session_id)
        if not poll:
            flash(f"Опрос {session_id} не найден", "error")
            return redirect(url_for("core.teacher_dashboard"))
    except Exception as e:
        current_app.logger.error(
            f"Error fetching poll {session_id} for results page: {e}"
        )
        db.session.rollback()
        flash("Ошибка при доступе к опросу.", "error")
        return redirect(url_for("core.teacher_dashboard"))

    return render_template("results.html", session_id=poll.id, poll_data=poll)


@core_bp.route("/end_poll/<session_id>", methods=["POST"])
@login_required
def end_poll_route(session_id):
    try:
        poll = db.session.get(Poll, session_id)
        if poll and poll.is_active:
            poll.is_active = False
            db.session.commit()
            flash(f"Опрос {session_id} успешно завершен.", "success")
            current_app.logger.info(
                f"Poll {session_id} ended by user {current_user.username}"
            )
            try:
                socketio.emit("poll_ended", {"session_id": session_id}, room=session_id)
                current_app.logger.info(
                    f"SocketIO emitted 'poll_ended' to room {session_id}"
                )
            except Exception as e_socket:
                current_app.logger.error(
                    f"Error emitting poll_ended WebSocket for {session_id}: {e_socket}"
                )
        elif poll:
            flash(f"Опрос {session_id} уже был завершен.", "info")
        else:
            flash(f"Опрос {session_id} не найден.", "warning")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error ending poll {session_id}: {e}")
        flash("Ошибка при завершении опроса.", "error")
    return redirect(url_for("core.teacher_dashboard"))


@core_bp.route("/export/<session_id>")
@login_required
def export_results(session_id):
    try:
        poll = db.session.get(Poll, session_id)
        if not poll:
            current_app.logger.warning(f"Export: Poll {session_id} not found.")
            return render_template("error.html", message="Опрос не найден."), 404
    except Exception as e:
        current_app.logger.error(f"Error fetching poll {session_id} for export: {e}")
        db.session.rollback()
        return (
            render_template("error.html", message="Ошибка при доступе к опросу."),
            500,
        )

    si = io.StringIO()
    cw = csv.writer(si)
    filename = f"results_{poll.poll_type}_{session_id}.csv"

    if poll.poll_type == "pulse":
        header = ["Вариант Ответа", "Количество Голосов"]
        cw.writerow(header)
        agg_results = poll.get_aggregated_results()
        options = poll.get_options()
        for option in options:
            cw.writerow([option, agg_results.get(option, 0)])
    elif poll.poll_type == "feedback":
        questions = poll.get_questions()
        question_map = {q.get("id"): q for q in questions}

        header = ["Имя (если указано)"]
        scale_headers_info = []
        text_headers_info = []

        for q_id, q_data in question_map.items():
            q_text = q_data.get("text", f"Вопрос ID {q_id}")
            if q_data.get("type") == "scale":
                scale_headers_info.append((q_id, f"{q_text} (Шкала)"))
            elif q_data.get("type") == "text":
                text_headers_info.append((q_id, f"{q_text} (Текст)"))

        header.extend([h[1] for h in scale_headers_info])
        header.extend([h[1] for h in text_headers_info])
        cw.writerow(header)

        all_text_responses_structured = []

        user_responses = {}
        text_responses_raw = poll.get_text_responses()

        # TODO: Этот блок логики экспорта для feedback нужно тщательно продумать.
        # Как лучше представить смешанные данные?
        # Пока что упрощенный вариант - просто все текстовые ответы по одному на строку.
        # Агрегированные шкалы можно добавить отдельными строками или столбцами.

        # Вариант 1: Каждая строка - один текстовый ответ.
        # Проблема: если у пользователя несколько текстовых ответов на разные вопросы.
        # Вариант 2: Каждая строка - один пользователь (если имя есть).
        # Агрегированные шкалы как отдельные строки в конце.

        # Запишем сначала все текстовые ответы
        if text_headers_info:  # Если есть текстовые вопросы
            temp_header_texts_only = ["Имя (если указано)"] + [
                h[1] for h in text_headers_info
            ]
            # cw.writerow(["--- Текстовые ответы ---"])
            # cw.writerow(temp_header_texts_only)

            responses_by_user = {}
            for resp_item in text_responses_raw:
                user_key = resp_item.get("name")
                if user_key not in responses_by_user:
                    responses_by_user[user_key] = {}
                responses_by_user[user_key][resp_item.get("question_id")] = (
                    resp_item.get("text", "")
                )

            for user_key, answers in responses_by_user.items():
                row = [user_key if user_key else "Анонимно"]
                row.extend([""] * len(scale_headers_info))
                for q_id_text, _ in text_headers_info:
                    row.append(answers.get(q_id_text, ""))
                cw.writerow(row)

        if scale_headers_info:
            agg_scale_results = poll.get_aggregated_results()
            for q_id_scale, header_text_scale in scale_headers_info:
                q_data = question_map.get(q_id_scale)
                if not q_data or q_data.get("type") != "scale":
                    continue

                options = q_data.get("options", [])
                q_results = agg_scale_results.get(q_id_scale, {})

                # cw.writerow([f"Шкала: {header_text_scale}"])
                # cw.writerow(["Вариант", "Количество"])
                for opt in options:
                    # Строка для экспорта: пустые ячейки для имени и текстовых вопросов,
                    # затем данные по шкале. Это не очень красиво.
                    # Лучше иметь отдельные секции в CSV или полностью перестроить логику.
                    # Пока оставим так, как было в вашем коде - каждый вопрос шкалы как бы сам по себе
                    # Это не будет выровнено с текстовыми ответами по пользователям.
                    # Для корректного CSV лучше иметь одну структуру строк.
                    # Простой вывод:
                    # cw.writerow([header_text_scale, opt, q_results.get(opt, 0)])
                    # Это нарушит общую структуру header.
                    pass  # Пропускаем пока, чтобы не ломать структуру. Экспорт фидбека сложен.
            # TODO: Переделать экспорт Feedback для CSV более осмысленно.
            # Возможно, для каждого пользователя (если есть имя) выводить его текстовые ответы,
            # а для вопросов-шкал выводить агрегированные данные отдельным блоком или
            # пытаться сопоставить, если есть ID пользователя и он отвечал на шкалы (но у нас нет такого ID).

            # Пока что, для сохранения общей структуры, если есть и текстовые, и шкалы,
            # то после текстовых строк добавим строки для каждой шкалы.
            # Это не будет "одна строка - один респондент".
            if scale_headers_info and text_headers_info:
                cw.writerow([])  # Пустая строка-разделитель
                cw.writerow(
                    ["--- Агрегированные ответы по шкалам ---"]
                    + [""] * (len(header) - 1)
                )

            for q_id_scale, header_text_scale in scale_headers_info:
                q_data = question_map.get(q_id_scale)
                options = q_data.get("options", [])
                q_results = agg_scale_results.get(q_id_scale, {})
                cw.writerow(
                    [f"Вопрос (Шкала): {q_data.get('text')}"] + [""] * (len(header) - 1)
                )
                for opt in options:
                    row_scale = [""] * len(header)
                    try:
                        col_idx = header.index(header_text_scale)
                        row_scale[col_idx] = f"{opt}: {q_results.get(opt, 0)}"
                    except ValueError:
                        row_scale[0] = opt
                        row_scale[1] = q_results.get(opt, 0)
                    # Это все еще не идеально. Лучше переформатировать структуру CSV для Feedback.
                    # В текущей реализации, если есть и текст и шкалы, будет мешанина.
                    # Предлагаю пока такой формат для шкал, если они идут после текста:
                cw.writerow([f"Шкала: {q_data.get('text')}"] + [""] * (len(header) - 1))
                for opt_idx, opt_val in enumerate(options):
                    row_opt = [""] * len(header)
                    row_opt[0] = f" - {opt_val}"
                    row_opt[1] = q_results.get(opt_val, 0)
                    cw.writerow(row_opt)
                cw.writerow([])
    else:
        current_app.logger.error(
            f"Export called for unknown poll type {poll.poll_type} (ID: {session_id})"
        )
        return (
            render_template(
                "error.html", message="Неизвестный тип опроса для экспорта."
            ),
            500,
        )

    output = si.getvalue()
    response = Response(
        output.encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
    return response


@core_bp.route("/qr/<session_id>")
@login_required
def qr_code_display(session_id):
    try:
        poll = db.session.get(Poll, session_id)
    except Exception as e:
        current_app.logger.error(
            f"Error fetching poll {session_id} for QR display: {e}"
        )
        db.session.rollback()
        flash("Ошибка при доступе к опросу.", "error")
        return redirect(url_for("core.teacher_dashboard"))

    if not poll:
        flash(f"Опрос {session_id} не найден.", "error")
        return redirect(url_for("core.teacher_dashboard"))
    if not poll.qr_code_url:
        flash(f"QR-код для опроса {session_id} не найден.", "error")
        return redirect(url_for("core.teacher_dashboard"))
    if not poll.is_active:
        flash(f"Опрос {session_id} уже завершен.", "warning")

    display_student_url = poll.student_url
    public_base_now = current_app.config.get("PUBLIC_BASE_URL")
    if public_base_now and (
        "127.0.0.1" in display_student_url or "localhost" in display_student_url
    ):
        public_base_now = public_base_now.rstrip("/")
        poll_path = url_for(
            "core.student_poll_page", session_id=session_id, _external=False
        )
        corrected_url = f"{public_base_now}{poll_path}"
        current_app.logger.info(
            f"QR Display: Correcting display URL for old poll {session_id} "
            f"from '{display_student_url}' to '{corrected_url}' based on current PUBLIC_BASE_URL."
        )
        display_student_url = corrected_url
    elif not public_base_now and (
        "127.0.0.1" in display_student_url or "localhost" in display_student_url
    ):
        current_app.logger.warning(
            f"QR Display: Poll {session_id} URL is local ({display_student_url}) "
            f"and PUBLIC_BASE_URL is not currently set. Displaying local URL."
        )

    current_app.logger.info(
        f"Displaying QR page for poll {session_id}, URL under QR: {display_student_url}"
    )
    return render_template(
        "qr_code_display.html",
        poll=poll,
        display_url=display_student_url,
        qr_url=poll.qr_code_url,
    )


@core_bp.route("/delete_poll/<session_id>", methods=["POST"])
@login_required
def delete_poll_route(session_id):
    try:
        poll_to_delete = db.session.get(Poll, session_id)
        if poll_to_delete:
            qr_filepath_to_delete = None
            if poll_to_delete.qr_code_url:
                try:
                    qr_filename_in_static = poll_to_delete.qr_code_url.split("/")[-1]
                    core_bp_static_folder = current_app.blueprints["core"].static_folder
                    if not core_bp_static_folder:
                        core_bp_root_path = current_app.blueprints["core"].root_path
                        core_bp_static_folder = os.path.join(
                            core_bp_root_path, "static"
                        )

                    qr_folder_path = os.path.join(
                        core_bp_static_folder, QR_CODES_FOLDER_NAME
                    )
                    qr_filepath_to_delete = os.path.join(
                        qr_folder_path, qr_filename_in_static
                    )
                    current_app.logger.info(
                        f"Determined QR file path for deletion: {qr_filepath_to_delete}"
                    )
                except Exception as e_path:
                    current_app.logger.warning(
                        f"Could not determine QR file path for {session_id}: {e_path}"
                    )

            poll_id_log = poll_to_delete.id
            db.session.delete(poll_to_delete)
            db.session.commit()
            flash(f"Опрос (ID: {poll_id_log}) успешно удален.", "success")
            current_app.logger.info(
                f"Poll {poll_id_log} deleted by user {current_user.username}"
            )

            if qr_filepath_to_delete and os.path.exists(qr_filepath_to_delete):
                try:
                    os.remove(qr_filepath_to_delete)
                    current_app.logger.info(
                        f"Deleted associated QR file: {qr_filepath_to_delete}"
                    )
                except OSError as e_qr:
                    current_app.logger.error(
                        f"Could not delete QR file {qr_filepath_to_delete} for deleted poll {poll_id_log}: {e_qr}"
                    )
            elif qr_filepath_to_delete:
                current_app.logger.warning(
                    f"QR file for deleted poll {poll_id_log} not found for deletion: {qr_filepath_to_delete}"
                )
        else:
            flash("Опрос не найден.", "warning")
            current_app.logger.warning(
                f"Delete attempt failed: Poll {session_id} not found."
            )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting poll {session_id}: {e}")
        flash("Ошибка при удалении опроса.", "error")
    return redirect(url_for("core.teacher_dashboard"))


# --- Маршруты для Управления Шаблонами ---
@core_bp.route("/templates/manage", methods=["GET"])
@login_required
def manage_poll_templates():
    current_app.logger.info(
        f"User {current_user.username} accessed template management page."
    )
    try:
        all_templates_db = (
            db.session.execute(
                db.select(PollTemplate).order_by(
                    PollTemplate.poll_type, PollTemplate.name
                )
            )
            .scalars()
            .all()
        )
        pulse_templates_list = [t for t in all_templates_db if t.poll_type == "pulse"]
        feedback_templates_list = [
            t for t in all_templates_db if t.poll_type == "feedback"
        ]
    except Exception as e:
        current_app.logger.error(
            f"Error fetching poll templates for management page: {e}"
        )
        db.session.rollback()
        flash("Не удалось загрузить список шаблонов.", "error")
        pulse_templates_list, feedback_templates_list = [], []
    return render_template(
        "manage_templates.html",
        pulse_templates=pulse_templates_list,
        feedback_templates=feedback_templates_list,
    )


@core_bp.route(
    "/templates/create/",
    defaults={"template_id": None, "poll_type_arg": None},
    methods=["GET", "POST"],
)
@core_bp.route(
    "/templates/create/<poll_type_arg>",
    defaults={"template_id": None},
    methods=["GET", "POST"],
)
@core_bp.route(
    "/templates/edit/<int:template_id>",
    defaults={"poll_type_arg": None},
    methods=["GET", "POST"],
)
@login_required
def create_edit_poll_template(template_id, poll_type_arg):
    template = None
    form_action_url = ""
    page_title = ""
    # Данные для формы (при GET или ошибке POST)
    # Изначально пустые или дефолтные для создания, либо из template для редактирования
    current_form_data = {}

    if template_id:  # Редактирование
        template = db.session.get(PollTemplate, template_id)
        if not template:
            flash("Шаблон не найден.", "error")
            return redirect(url_for("core.manage_poll_templates"))
        poll_type = template.poll_type
        page_title = f"Редактирование шаблона: {template.name}"
        form_action_url = url_for(
            "core.create_edit_poll_template", template_id=template_id
        )
        current_form_data = template.get_data() if template else {}
        if poll_type == "feedback" and not isinstance(
            current_form_data, list
        ):  # Гарантируем список для feedback
            current_app.logger.warning(
                f"Feedback template data for ID {template_id} was not a list. Resetting."
            )
            current_form_data = []
        elif poll_type == "pulse" and not isinstance(
            current_form_data, dict
        ):  # Гарантируем словарь для pulse
            current_app.logger.warning(
                f"Pulse template data for ID {template_id} was not a dict. Resetting."
            )
            current_form_data = {}
    elif poll_type_arg in ["pulse", "feedback"]:  # Создание
        poll_type = poll_type_arg
        page_title = (
            f"Создание шаблона {'Пульс' if poll_type == 'pulse' else 'Обратная Связь'}"
        )
        form_action_url = url_for(
            "core.create_edit_poll_template", poll_type_arg=poll_type
        )
        if poll_type == "pulse":
            current_form_data = {
                "question": DEFAULT_PULSE_QUESTION,
                "options": list(DEFAULT_PULSE_OPTIONS),
            }
        else:  # feedback
            current_form_data = [
                q.copy() for q in DEFAULT_FEEDBACK_QUESTIONS
            ]  # Копируем, чтобы не изменять оригинал
    else:
        flash("Не указан тип создаваемого шаблона.", "warning")
        return redirect(url_for("core.manage_poll_templates"))

    if request.method == "POST":
        name = request.form.get("template_name", "").strip()
        description = request.form.get("template_description", "").strip()
        # Восстанавливаем current_form_data из request.form для случая ошибки, чтобы предзаполнить поля
        # Это сложная часть для динамических полей, JS должен помочь правильно структурировать данные
        # или мы должны парсить request.form более детально.
        # Пока что, если POST неудачный, current_form_data НЕ будет обновлен из request.form в этом блоке,
        # он останется тем, что был при GET. Это нужно улучшить для лучшего UX при ошибках.

        if not name:
            flash("Название шаблона не может быть пустым.", "error")
            # Передаем обратно current_form_data, который был на момент GET
            return render_template(
                "create_edit_template.html",
                page_title=page_title,
                form_action_url=form_action_url,
                template=(
                    {"name": name, "description": description} if template_id else None
                ),  # Для имени/описания
                poll_type=poll_type,
                template_data_for_form=current_form_data,  # <--- Используем current_form_data
                DEFAULT_PULSE_QUESTION_JS=DEFAULT_PULSE_QUESTION,
                DEFAULT_PULSE_OPTIONS_JS=DEFAULT_PULSE_OPTIONS,
                DEFAULT_FEEDBACK_QUESTIONS_JS=DEFAULT_FEEDBACK_QUESTIONS,
            )
        data_to_save = {}
        if poll_type == "pulse":
            question = request.form.get("pulse_question", "").strip()
            options = [
                opt.strip()
                for opt in request.form.getlist("pulse_options[]")
                if opt.strip()
            ]
            if not question or len(options) < 2:
                flash(
                    'Для шаблона "Пульс" нужен вопрос и минимум два варианта ответа.',
                    "error",
                )
                # Здесь нужно собрать 'восстановленные' данные из формы для передачи обратно
                restored_pulse_data = {
                    "question": question,
                    "options": options if options else [],
                }
                return render_template(
                    "create_edit_template.html",
                    page_title=page_title,
                    form_action_url=form_action_url,
                    template=(
                        {"name": name, "description": description}
                        if template_id
                        else None
                    ),
                    poll_type=poll_type,
                    template_data_for_form=restored_pulse_data,  # <--- Передаем восстановленные
                    DEFAULT_PULSE_QUESTION_JS=DEFAULT_PULSE_QUESTION,
                    DEFAULT_PULSE_OPTIONS_JS=DEFAULT_PULSE_OPTIONS,
                    DEFAULT_FEEDBACK_QUESTIONS_JS=DEFAULT_FEEDBACK_QUESTIONS,
                )
            data_to_save = {"question": question, "options": options}
        elif poll_type == "feedback":
            questions_list = []
            texts = request.form.getlist("fb_question_texts[]")
            types = request.form.getlist("fb_question_types[]")
            # Восстанавливаем current_form_data для feedback при ошибке
            restored_feedback_data = []

            if not texts:
                flash(
                    'Для шаблона "Обратная связь" должен быть хотя бы один вопрос.',
                    "error",
                )
                return render_template(
                    "create_edit_template.html",
                    page_title=page_title,
                    form_action_url=form_action_url,
                    template=(
                        {"name": name, "description": description}
                        if template_id
                        else None
                    ),
                    poll_type=poll_type,
                    template_data_for_form=restored_feedback_data,  # Пустой список, если нет текстов
                    DEFAULT_PULSE_QUESTION_JS=DEFAULT_PULSE_QUESTION,
                    DEFAULT_PULSE_OPTIONS_JS=DEFAULT_PULSE_OPTIONS,
                    DEFAULT_FEEDBACK_QUESTIONS_JS=DEFAULT_FEEDBACK_QUESTIONS,
                )
            for i in range(len(texts)):
                q_text = texts[i].strip()
                q_type = (
                    types[i] if i < len(types) else "text"
                )  # Фолбэк, если типов меньше
                q_id = f"gen_q_{uuid.uuid4().hex[:6]}"
                q_data_for_save = {"id": q_id, "text": q_text, "type": q_type}
                q_data_for_restore = q_data_for_save.copy()

                if not q_text:
                    continue  # Пропускаем пустые вопросы при сохранении

                if q_type == "scale":
                    scale_options_raw = request.form.getlist(f"fb_q_options_{i}[]")
                    scale_options = [
                        opt.strip() for opt in scale_options_raw if opt.strip()
                    ]
                    if len(scale_options) < 2:
                        flash(
                            f'Для вопроса-шкалы "{q_text or f"Вопрос #{i+1}"}" нужно минимум два варианта.',
                            "warning",
                        )
                        # Не добавляем этот вопрос в data_to_save, но можем добавить в restored_feedback_data
                        q_data_for_restore["options"] = (
                            scale_options  # Сохраняем введенные опции для восстановления
                        )
                        restored_feedback_data.append(q_data_for_restore)
                        continue  # Пропускаем этот вопрос для сохранения
                    q_data_for_save["options"] = scale_options
                    q_data_for_restore["options"] = scale_options
                questions_list.append(q_data_for_save)
                restored_feedback_data.append(q_data_for_restore)

            if not questions_list:  # Если все вопросы были невалидны
                flash(
                    "Не удалось сформировать ни одного валидного вопроса для шаблона ОС.",
                    "error",
                )
                return render_template(
                    "create_edit_template.html",
                    page_title=page_title,
                    form_action_url=form_action_url,
                    template=(
                        {"name": name, "description": description}
                        if template_id
                        else None
                    ),
                    poll_type=poll_type,
                    template_data_for_form=restored_feedback_data,  # Восстановленные данные
                    DEFAULT_PULSE_QUESTION_JS=DEFAULT_PULSE_QUESTION,
                    DEFAULT_PULSE_OPTIONS_JS=DEFAULT_PULSE_OPTIONS,
                    DEFAULT_FEEDBACK_QUESTIONS_JS=DEFAULT_FEEDBACK_QUESTIONS,
                )
            data_to_save = questions_list
        try:
            if template_id:
                template.name = name
                template.description = description
                template.data_json = json.dumps(data_to_save, ensure_ascii=False)
                flash_msg = f"Шаблон '{name}' обновлен."
            else:
                if db.session.execute(
                    db.select(PollTemplate).filter_by(name=name)
                ).scalar_one_or_none():
                    flash(f"Шаблон с именем '{name}' уже существует.", "error")
                    # Передаем все введенные данные обратно
                    # current_form_data здесь будет data_to_save, но пересобранное
                    return render_template(
                        "create_edit_template.html",
                        page_title=page_title,
                        form_action_url=form_action_url,
                        template={"name": name, "description": description},
                        poll_type=poll_type,
                        template_data_for_form=(
                            data_to_save
                            if poll_type == "feedback"
                            else {
                                "question": data_to_save.get("question"),
                                "options": data_to_save.get("options", []),
                            }
                        ),
                        DEFAULT_PULSE_QUESTION_JS=DEFAULT_PULSE_QUESTION,
                        DEFAULT_PULSE_OPTIONS_JS=DEFAULT_PULSE_OPTIONS,
                        DEFAULT_FEEDBACK_QUESTIONS_JS=DEFAULT_FEEDBACK_QUESTIONS,
                    )

                new_template = PollTemplate(
                    name=name,
                    description=description,
                    poll_type=poll_type,
                    data_json=json.dumps(data_to_save, ensure_ascii=False),
                )
                db.session.add(new_template)
                flash_msg = f"Шаблон '{name}' создан."
            db.session.commit()
            flash(flash_msg, "success")
            return redirect(url_for("core.manage_poll_templates"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error saving template '{name}': {e}")
            flash("Ошибка при сохранении шаблона.", "error")
            # При ошибке сохранения, передаем введенные данные обратно
            return render_template(
                "create_edit_template.html",
                page_title=page_title,
                form_action_url=form_action_url,
                template=(
                    {"name": name, "description": description} if template_id else None
                ),
                poll_type=poll_type,
                template_data_for_form=(
                    data_to_save
                    if poll_type == "feedback"
                    else {
                        "question": data_to_save.get("question"),
                        "options": data_to_save.get("options", []),
                    }
                ),
                DEFAULT_PULSE_QUESTION_JS=DEFAULT_PULSE_QUESTION,
                DEFAULT_PULSE_OPTIONS_JS=DEFAULT_PULSE_OPTIONS,
                DEFAULT_FEEDBACK_QUESTIONS_JS=DEFAULT_FEEDBACK_QUESTIONS,
            )
    # Для GET запроса
    return render_template(
        "create_edit_template.html",
        page_title=page_title,
        form_action_url=form_action_url,
        template=template,
        poll_type=poll_type,
        template_data_for_form=current_form_data,  # Используем подготовленные данные
        DEFAULT_PULSE_QUESTION_JS=DEFAULT_PULSE_QUESTION,
        DEFAULT_PULSE_OPTIONS_JS=DEFAULT_PULSE_OPTIONS,
        DEFAULT_FEEDBACK_QUESTIONS_JS=DEFAULT_FEEDBACK_QUESTIONS,
    )


@core_bp.route("/templates/delete/<int:template_id>", methods=["POST"])
@login_required
def delete_poll_template(template_id):
    template = db.session.get(PollTemplate, template_id)
    if template:
        try:
            template_name = template.name
            db.session.delete(template)
            db.session.commit()
            flash(f"Шаблон '{template_name}' успешно удален.", "success")
            current_app.logger.info(
                f"PollTemplate '{template_name}' (ID: {template_id}) deleted by user {current_user.username}."
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error deleting PollTemplate ID {template_id}: {e}"
            )
            flash("Ошибка при удалении шаблона.", "error")
    else:
        flash("Шаблон не найден.", "warning")
    return redirect(url_for("core.manage_poll_templates"))


# --- Обработчики SocketIO ---
@socketio.on("connect")
def handle_connect():  # ... (без изменений) ...
    sid = request.sid
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "Unknown"
    client_ips[sid] = client_ip
    current_app.logger.info(f"Client connected: SID={sid}, IP={client_ip}")
    emit("your_ip", {"ip": client_ip}, to=sid)


@socketio.on("disconnect")
def handle_disconnect():  # ... (без изменений) ...
    sid = request.sid
    removed_ip = client_ips.pop(sid, None)
    current_app.logger.info(
        f'Client disconnected: SID={sid}, IP={removed_ip if removed_ip else "N/A"}'
    )


@socketio.on("join_room")
def handle_join_room(
    data,
):  # ... (без изменений, но с with current_app.app_context()) ...
    sid = request.sid
    room = data.get("room")
    client_ip = client_ips.get(sid, "Unknown")
    if not room:
        return
    join_room(room)
    current_app.logger.info(f"Client {sid} (IP: {client_ip}) joined room {room}")
    try:
        with current_app.app_context():  # Для доступа к БД в SocketIO обработчике
            poll = db.session.get(Poll, room)
            if poll:
                payload = poll.get_results_payload()
                emit("results_updated", payload, to=sid)
                if not poll.is_active:
                    emit("poll_ended", {"session_id": room}, to=sid)
            else:
                emit("poll_error", {"message": "Опрос не найден"}, to=sid)
    except Exception as e:
        current_app.logger.error(
            f"Error sending initial results to {sid} for {room}: {e}"
        )
        emit("poll_error", {"message": "Ошибка загрузки данных опроса"}, to=sid)


def setup_database_and_admin(app_instance):
    with app_instance.app_context():
        app_instance.logger.info(
            "Attempting to create database tables for ALL BINDS..."
        )
        try:
            db.create_all()
            app_instance.logger.info(
                "Database tables checked/created successfully for all binds."
            )
        except Exception as e:
            app_instance.logger.critical(
                f"CRITICAL ERROR: Failed to create/check database tables: {e}"
            )
            import traceback

            app_instance.logger.error(traceback.format_exc())
            raise

        # --- Создание или проверка Суперадмина ---
        sa_username = app_instance.config.get("DEFAULT_SUPERADMIN_USER")
        sa_password = app_instance.config.get("DEFAULT_SUPERADMIN_PASS")

        # Используем сессию SQLAlchemy для запросов
        super_admin = db.session.execute(
            db.select(SuperAdmin).filter_by(username=sa_username)
        ).scalar_one_or_none()

        if not super_admin:
            try:
                app_instance.logger.info(
                    f"SuperAdmin user '{sa_username}' not found. Creating..."
                )
                new_super_admin = SuperAdmin(username=sa_username)
                new_super_admin.set_password(sa_password)
                db.session.add(new_super_admin)
                db.session.commit()
                app_instance.logger.info(
                    f"SuperAdmin user '{sa_username}' created successfully."
                )
            except Exception as e:
                db.session.rollback()
                app_instance.logger.error(
                    f"Failed to create SuperAdmin user '{sa_username}': {e}"
                )
        else:
            app_instance.logger.info(
                f"SuperAdmin user '{sa_username}' already exists. Checking password (for dev/debug)..."
            )
            # ВНИМАНИЕ: Не проверяйте пароль так в проде, если он может меняться.
            # Это просто для отладки, чтобы убедиться, что дефолтный пароль работает, если он не менялся.
            if not super_admin.check_password(
                sa_password
            ) and sa_password == app_instance.config.get("DEFAULT_SUPERADMIN_PASS"):
                app_instance.logger.warning(
                    f"Password for existing SuperAdmin '{sa_username}' does NOT match DEFAULT_SUPERADMIN_PASS. "
                    f"This might be ok if it was changed. Or .env password is not picked up."
                )
