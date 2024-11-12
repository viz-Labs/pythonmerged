from django.urls import path
from . import views

urlpatterns = [
    path('api/ask/', views.api_ask, name='api_ask'),  # Add the route for your API
]