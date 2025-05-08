# app\models.py
import json
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.sql import func
from markupsafe import Markup


db = SQLAlchemy()


class Poll(db.Model):
    __tablename__ = "polls"

    id = db.Column(db.String(8), primary_key=True)
    poll_type = db.Column(db.String(10), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=func.now())
    pulse_question = db.Column(db.String(500))
    pulse_options = db.Column(db.Text)
    feedback_questions = db.Column(db.Text)
    aggregated_results = db.Column(db.Text, default="{}")
    response_count = db.Column(db.Integer, default=0, nullable=False)
    student_url = db.Column(db.String(255))
    qr_code_url = db.Column(db.String(100))
    text_feedback_responses = db.Column(db.Text, default='[]')

    def __repr__(self):
        return f"<Poll {self.id} ({self.poll_type})>"

    def set_options(self, options_list):
        self.pulse_options = (
            json.dumps(options_list, ensure_ascii=False) if options_list else None
        )

    def get_options(self):
        from flask import current_app

        try:
            return json.loads(self.pulse_options) if self.pulse_options else []
        except json.JSONDecodeError:
            current_app.logger.error(
                f"JSONDecodeError in get_options for Poll {self.id}"
            )
            return []

    def set_questions(self, questions_list):
        self.feedback_questions = (
            json.dumps(questions_list, ensure_ascii=False) if questions_list else None
        )

    def get_questions(self):
        from flask import current_app

        try:
            return (
                json.loads(self.feedback_questions) if self.feedback_questions else []
            )
        except json.JSONDecodeError:
            current_app.logger.error(
                f"JSONDecodeError in get_questions for Poll {self.id}"
            )
            return []

    def get_aggregated_results(self):
        from flask import current_app

        try:
            return (
                json.loads(self.aggregated_results) if self.aggregated_results else {}
            )
        except json.JSONDecodeError:
            current_app.logger.error(
                f"JSONDecodeError in get_aggregated_results for Poll {self.id}"
            )
            return {}

    def update_aggregated_results(self, answer, question_id=None):
        from flask import current_app

        results = self.get_aggregated_results()
        if self.poll_type == "pulse":
            results[answer] = results.get(answer, 0) + 1
        elif self.poll_type == "feedback" and question_id:
            question_info = next(
                (q for q in self.get_questions() if q.get("id") == question_id), None
            )
            if question_info and question_info.get("type") == "scale":
                if question_id not in results:
                    results[question_id] = {}
                results[question_id][answer] = results[question_id].get(answer, 0) + 1
        self.aggregated_results = json.dumps(results, ensure_ascii=False)

    def add_text_response(self, text, name, question_id, is_flagged=False):
        from flask import current_app

        try:
            responses = (
                json.loads(self.text_feedback_responses)
                if self.text_feedback_responses
                else []
            )
        except json.JSONDecodeError:
            current_app.logger.error(
                f"JSONDecodeError in add_text_response (loading) for Poll {self.id}"
            )
            responses = []
        responses.append(
            {
                "text": text,
                "name": name,
                "question_id": question_id,
                "is_flagged": is_flagged,
            }
        )
        self.text_feedback_responses = json.dumps(responses, ensure_ascii=False)

    def get_text_responses(self):
        from flask import current_app

        try:
            return (
                json.loads(self.text_feedback_responses)
                if self.text_feedback_responses
                else []
            )
        except json.JSONDecodeError:
            current_app.logger.error(
                f"JSONDecodeError in get_text_responses for Poll {self.id}"
            )
            return []

    def get_results_payload(self):
        """Собирает данные для отправки через WebSocket или для API."""
        response_data = {
            "session_id": self.id,
            "poll_type": self.poll_type,
            "total_responses": self.response_count,
            "is_active": self.is_active,
            "pulse_data": None,
            "feedback_data": {"questions": [], "charts": {}, "texts": []},
        }
        agg_results_dict = self.get_aggregated_results()

        if self.poll_type == "pulse":
            options = self.get_options()
            pulse_labels = options
            pulse_values = [agg_results_dict.get(option, 0) for option in pulse_labels]
            response_data["pulse_data"] = {
                "question": self.pulse_question,
                "labels": pulse_labels,
                "values": pulse_values,
            }
        elif self.poll_type == "feedback":
            questions = self.get_questions()
            response_data["feedback_data"]["questions"] = questions
            response_data["feedback_data"]["texts"] = self.get_text_responses()

            for question in questions:
                q_id = question.get("id")
                q_type = question.get("type")
                if q_id and q_type == "scale":
                    scale_options = list(question.get("options", []))
                    q_agg_results = agg_results_dict.get(q_id, {})
                    chart_labels = scale_options
                    chart_values = [
                        q_agg_results.get(option, 0) for option in scale_options
                    ]
                    response_data["feedback_data"]["charts"][q_id] = {
                        "labels": chart_labels,
                        "values": chart_values,
                    }
        return response_data


class PollTemplate(db.Model):
    __tablename__ = "poll_templates"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    poll_type = db.Column(db.String(10), nullable=False)
    description = db.Column(db.String(300))
    data_json = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return f"<PollTemplate {self.name} ({self.poll_type})>"

    def get_data(self):
        from flask import current_app

        try:
            return json.loads(self.data_json)
        except json.JSONDecodeError:
            current_app.logger.error(
                f"JSONDecodeError in PollTemplate.get_data for ID {self.id}"
            )
            return None


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"
