import os
import json
from urllib.parse import urlparse, urljoin
from markupsafe import Markup
from flask import request, current_app
from better_profanity import profanity


DEFAULT_PULSE_QUESTION = "Как вы себя чувствуете перед уроком?"
DEFAULT_PULSE_OPTIONS = ["Отлично 😊", "Нормально 😐", "Устал(а) 😞", "Не готов(а) 🤯"]
DEFAULT_FEEDBACK_QUESTIONS = [
    {'id': 'q1', 'text': 'Насколько понятен был материал?', 'type': 'scale', 'options': ['1 (Совсем нет)', '2', '3', '4', '5 (Полностью)']},
    {'id': 'q2', 'text': 'Что было самым полезным или интересным?', 'type': 'text'},
    {'id': 'q3', 'text': 'Что вызвало трудности?', 'type': 'text'},
]

QR_CODES_FOLDER_NAME = 'qrcodes'
_utils_dir = os.path.dirname(os.path.abspath(__file__))
RUSSIAN_PROFANITY_FILE = os.path.join(_utils_dir, 'static', 'profanity_list_ru.txt') 

print(f"INFO: Attempting to load custom Russian profanity words from: {RUSSIAN_PROFANITY_FILE}")
if os.path.exists(RUSSIAN_PROFANITY_FILE):
    try:
        profanity.load_censor_words_from_file(RUSSIAN_PROFANITY_FILE)
        print(f"INFO [profanity_setup]: Successfully loaded RUSSIAN profanity words from file.") 
    except Exception as e:
        print(f"ERROR: Could not load Russian profanity words from {RUSSIAN_PROFANITY_FILE}: {e}")
else:
    print(f"WARNING: Russian profanity file not found at {RUSSIAN_PROFANITY_FILE}. Using default/English set if not cleared.")

def check_profanity_library(text):
    """
    Проверяет текст на нецензурную лексику с помощью better-profanity.
    Возвращает True, если найдено, иначе False.
    """
    if not text:
        return False
    return profanity.contains_profanity(text)

def censor_profanity_library(text):
    """
    Цензурирует нецензурную лексику в тексте с помощью better-profanity.
    Заменяет слова на звездочки (или указанный символ).
    """
    if not text:
        return ""
    return profanity.censor(text, censor_char='*')


def nl2br_filter(value):
    """Converts newlines in a string to HTML line breaks."""
    if value is None:
        return ''
    return Markup(str(value).replace('\n', '<br>\n'))


def is_safe_url(target):
    """
    Checks if a target URL is safe for redirection.
    It ensures that the target URL has the same scheme and netloc as the host URL.
    """
    current_app.logger.debug(f"is_safe_url: Checking target: {target}")
    current_app.logger.debug(f"is_safe_url: request.host_url: {request.host_url}")
    current_app.logger.debug(f"is_safe_url: request.url_root: {request.url_root}")

    if not request or not current_app:
        print("DEBUG: is_safe_url called outside request context or app not fully initialized.")
        return False

    try:
        public_base = current_app.config.get('PUBLIC_BASE_URL')
        if public_base:
            ref_url = urlparse(public_base.rstrip('/'))
        else:
            ref_url = urlparse(request.host_url)
        
        test_url = urlparse(urljoin(request.host_url, target))

        is_safe = test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc
        
        if not is_safe:
            current_app.logger.warning(
                f"Unsafe redirect URL detected: '{target}'. "
                f"Ref Host: {ref_url.netloc}, Test Host: {test_url.netloc}. "
                f"Request Host URL: {request.host_url}"
            )
        return is_safe
    except Exception as e:
        current_app.logger.error(f"Error in is_safe_url for target '{target}': {e}")
        return False


def get_base_url_for_links():
    """
    Определяет базовый URL для генерации ссылок для студентов.
    Приоритет: PUBLIC_BASE_URL из конфига.
    Запасной вариант: SERVER_NAME из конфига.
    Если ничего нет: пытается угадать из request (менее надежно вне контекста запроса).
    """
    if not current_app:
        public_base_env = os.environ.get('PUBLIC_BASE_URL')
        if public_base_env:
            return public_base_env.rstrip('/')
        return f"http://{os.environ.get('SERVER_IP', '127.0.0.1')}:{os.environ.get('SERVER_PORT', '5000')}"

    public_base = current_app.config.get('PUBLIC_BASE_URL')
    if public_base:
        base_url = public_base.rstrip('/')
        current_app.logger.info(f"Using PUBLIC_BASE_URL for links: {base_url}")
        return base_url
    
    server_name = current_app.config.get('SERVER_NAME')
    if server_name:
        scheme = 'https' if current_app.config.get('IS_HTTPS') else 'http'
        if request:
            scheme = request.scheme
        
        base_url = f"{scheme}://{server_name.rstrip('/')}"
        current_app.logger.info(f"Using SERVER_NAME for links: {base_url}")
        return base_url

    if request:
        base_url = request.url_root.rstrip('/')
        current_app.logger.warning(
            f"PUBLIC_BASE_URL and SERVER_NAME not set. "
            f"Falling back to guessed request.url_root: {base_url}. "
            f"External access via QR/Link might not work correctly!"
        )
        return base_url
    
    server_ip = os.environ.get('SERVER_IP', '127.0.0.1')
    server_port = os.environ.get('SERVER_PORT', '5000')
    scheme = 'http'
    base_url = f"{scheme}://{server_ip}:{server_port}"
    current_app.logger.critical(
        f"CRITICAL: Could not determine a reliable base URL. "
        f"Falling back to a very basic guess: {base_url}. "
        f"PLEASE SET PUBLIC_BASE_URL or SERVER_NAME in your environment/config."
    )
    return base_url