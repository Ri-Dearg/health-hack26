from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from equipment import live_views, views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/tag-read/", views.tag_read, name="tag_read"),
    path("api/bridge-read/", live_views.bridge_read, name="bridge_read"),
    path("dashboard/", live_views.dashboard, name="dashboard"),
    path("dashboard/feed/", live_views.dashboard_feed, name="dashboard_feed"),
    path("item/<str:code>/", views.item_page, name="item_page"),
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
]
