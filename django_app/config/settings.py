import os
from pathlib import Path
from dotenv import load_dotenv

# settings.py lives at django_app/config/settings.py
# The project root (where .env lives) is three levels up
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-local-dev-only-change-in-production")

DEBUG = True

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "webhook",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

WSGI_APPLICATION = "config.wsgi.application"

# No database needed
DATABASES = {}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Kafka
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
