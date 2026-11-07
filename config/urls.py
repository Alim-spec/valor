from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path

from funds import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('healthz/', views.healthz, name='healthz'),
    path('register/', views.register, name='register'),
    path('capital-accounts/', views.capital_accounts, name='capital_accounts'),
    path('investments/', views.investments, name='investments'),
    path('distributions/', views.distributions, name='distributions'),
    path('distributions/<int:pk>/', views.distribution, name='distribution'),
    path('distributions/<int:pk>/decide/', views.distribution_decide, name='distribution_decide'),
    path('fees/', views.fees, name='fees'),
    path('equalisation/', views.equalisation, name='equalisation'),
    path('service-levels/', views.service_levels, name='service_levels'),
    path('billing/', views.billing, name='billing'),
    path('acceptance/', views.acceptance, name='acceptance'),
    path('terms/', views.terms, name='terms'),
    path('audit/', views.audit, name='audit'),
    path('admin/', admin.site.urls),
]

admin.site.site_header = 'Valor Fund Administration'
admin.site.site_title = 'Valor'
