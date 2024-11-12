from django.contrib import admin
from django.urls import path, include  # Include function for app URLs

urlpatterns = [
    path('admin/', admin.site.urls),
    path('vizard/', include('vizard.urls')),  # Include the vizard app URLs
    path('csvupload/', include('csvupload.urls')),  # Include the csvupload app URLs
]