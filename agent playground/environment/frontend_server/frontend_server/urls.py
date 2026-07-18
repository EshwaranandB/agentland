"""frontend_server URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import include, path, re_path as url
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static

def translator_view(name):
    """Avoid importing the legacy renderer during AgentLand management checks."""
    def view(request, *args, **kwargs):
        from translator import views as translator_views
        return getattr(translator_views, name)(request, *args, **kwargs)
    return view

urlpatterns = [
    path('agentland/', include('agentland.urls')),
    url(r'^$', translator_view('landing'), name='landing'),
    url(r'^simulator_home$', translator_view('home'), name='home'),
    url(r'^demo/(?P<sim_code>[\w-]+)/(?P<step>[\w-]+)/(?P<play_speed>[\w-]+)/$', translator_view('demo'), name='demo'),
    url(r'^replay/(?P<sim_code>[\w-]+)/(?P<step>[\w-]+)/$', translator_view('replay'), name='replay'),
    url(r'^replay_persona_state/(?P<sim_code>[\w-]+)/(?P<step>[\w-]+)/(?P<persona_name>[\w-]+)/$', translator_view('replay_persona_state'), name='replay_persona_state'),
    url(r'^process_environment/$', translator_view('process_environment'), name='process_environment'),
    url(r'^update_environment/$', translator_view('update_environment'), name='update_environment'),
    url(r'^path_tester/$', translator_view('path_tester'), name='path_tester'),
    url(r'^path_tester_update/$', translator_view('path_tester_update'), name='path_tester_update'),
    path('admin/', admin.site.urls),
]
