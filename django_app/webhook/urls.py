from django.urls import path
from . import views

urlpatterns = [
    path("jira-webhook/", views.jira_webhook, name="jira-webhook"),
]
