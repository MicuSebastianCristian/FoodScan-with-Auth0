from django.urls import path
from . import views

urlpatterns = [
    path('', views.index_view, name='index'),
    path('home/', views.home, name='home'),
    path('scan/', views.scan, name='scan'),
    path('scan_result/<int:scan_id>/', views.scan_result, name='scan_result'),
    path('history/', views.history, name='history'),
    path('profile/', views.profile, name='profile'),
    path('ingredient/<str:ingredient_name>/', views.ingredient_info, name='ingredient_info'),
    path('login/', views.login, name='login'),
    path('logout/', views.logout, name='logout'),
    path('callback/', views.callback, name='callback'),
    path('calorie-tracker/', views.calorie_tracker_view, name='calorie_tracker'),
    path('calorie-tracker/delete/', views.delete_calorie_entries, name='delete_calorie_entries'),
] 