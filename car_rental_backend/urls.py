from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from agency.views import DashboardStatsView, CustomTokenObtainPairView, AgencyViewSet, UserViewSet, AgencySettingsView # استدعاء لوحة القيادة
from fleet.views import VehicleViewSet, BrandViewSet, ModelCarViewSet, PublicVehicleViewSet
from clients.views import ClientViewSet, ClientRegisterView, ClientAccountView
from contracts.views import ContractViewSet, PdfJobViewSet, BookingRequestViewSet, ReservationViewSet
from payments.views import PaymentViewSet
from expenses.views import ExpenseViewSet

router = DefaultRouter()
router.register(r'agencies', AgencyViewSet, basename='agency')
router.register(r'users', UserViewSet, basename='user')
router.register(r'vehicles', VehicleViewSet, basename='vehicle')
router.register(r'brands', BrandViewSet, basename='brand')
router.register(r'modelcars', ModelCarViewSet, basename='modelcar')
router.register(r'clients', ClientViewSet, basename='client')
router.register(r'contracts', ContractViewSet, basename='contract')
router.register(r'pdf-jobs', PdfJobViewSet, basename='pdf-job')
router.register(r'booking-requests', BookingRequestViewSet, basename='booking-request')
router.register(r'reservations', ReservationViewSet, basename='reservation')
router.register(r'payments', PaymentViewSet, basename='payment')
router.register(r'expenses', ExpenseViewSet, basename='expense')
router.register(r'public-vehicles', PublicVehicleViewSet, basename='public-vehicle')

urlpatterns =[
    path('admin/', admin.site.urls),
    
    # مسارات حساب العميل (Inscription + Profil) — avant le router pour éviter le conflit avec clients/{pk}/
    path('api/clients/register/', ClientRegisterView.as_view(), name='client_register'),
    path('api/clients/me/', ClientAccountView.as_view(), name='client_me'),
    
    # مسارات الـ API الأساسية
    path('api/', include(router.urls)),
    
    # مسارات تسجيل الدخول (JWT)
    path('api/token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # مسار الإحصائيات (Dashboard)
    path('api/dashboard/', DashboardStatsView.as_view(), name='dashboard_stats'),
    
    # مسار إعدادات الوكالة (Agency Settings)
    path('api/agency/settings/', AgencySettingsView.as_view(), name='agency_settings'),
]