from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from equipment import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/tag-read/", views.tag_read, name="tag_read"),
    path("item/<str:code>/", views.item_page, name="item_page"),
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
]
