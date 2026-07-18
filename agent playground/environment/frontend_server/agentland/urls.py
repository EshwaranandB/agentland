from django.urls import path

from . import views

urlpatterns = [
    path("", views.shell),
    path("demo/", views.demo),
    path("settings/openai/", views.openai_settings),
    path("sessions/", views.create_session),
    path("sessions/<uuid:session_id>/", views.workspace),
    path("reset-baseline/", views.reset_baseline),
    path("sessions/<uuid:session_id>/dispatch-builder/", views.dispatch_builder_view),
    path("sessions/<uuid:session_id>/snapshot/", views.snapshot),
    path("sessions/<uuid:session_id>/events/", views.events),
    path("sessions/<uuid:session_id>/city/", views.city),
    path("sessions/<uuid:session_id>/city-state/", views.city_state),
    path("sessions/<uuid:session_id>/presence/", views.heartbeat_presence),
    path("sessions/<uuid:session_id>/verification-request/", views.verification_request),
    path("sessions/<uuid:session_id>/artifacts/<uuid:artifact_id>/preview/", views.artifact_preview),
]
