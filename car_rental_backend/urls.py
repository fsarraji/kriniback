from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from agency.views import DashboardStatsView, CustomTokenObtainPairView, AgencyViewSet, UserViewSet, AgencySettingsView, AccountMeView, PublicAgencyViewSet, SubscriptionViewSet # استدعاء لوحة القيادة
from fleet.views import VehicleViewSet, BrandViewSet, ModelCarViewSet, PublicVehicleViewSet, EvaluationViewSet, VehicleCheckUniqueView
from fleet.gps_views import GpsPositionsView, GpsVehiclePositionView, GpsDevicesView, GpsHistoryView
from clients.views import ClientViewSet, ClientRegisterView, ClientAccountView, ClientCheckUniqueView
from contracts.views import ContractViewSet, PdfJobViewSet, BookingRequestViewSet, ReservationViewSet
from payments.views import PaymentViewSet
from expenses.views import ExpenseViewSet

router = DefaultRouter()
router.register(r'agencies', AgencyViewSet, basename='agency')
router.register(r'public-agencies', PublicAgencyViewSet, basename='public-agency')
router.register(r'users', UserViewSet, basename='user')
router.register(r'subscriptions', SubscriptionViewSet, basename='subscription')
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
router.register(r'evaluations', EvaluationViewSet, basename='evaluation')

urlpatterns =[
    path('admin/', admin.site.urls),
    
    # مسارات حساب العميل (Inscription + Profil) — avant le router pour éviter le conflit avec clients/{pk}/
    path('api/clients/register/', ClientRegisterView.as_view(), name='client_register'),
    path('api/clients/me/', ClientAccountView.as_view(), name='client_me'),
    path('api/clients/check-unique/', ClientCheckUniqueView.as_view(), name='client_check_unique'),

    # مسار التحقق من التفرد (الماريكول) — قبل الـ router لتجنب التعارض مع vehicles/{pk}/
    path('api/vehicles/check-unique/', VehicleCheckUniqueView.as_view(), name='vehicle_check_unique'),

    # مسار الحساب الشخصي للمستخدم المتصل — قبل الـ router لتجنب التعارض مع users/{pk}/
    path('api/users/me/', AccountMeView.as_view(), name='account_me'),

    # مسارات الـ API الأساسية
    path('api/', include(router.urls)),
    
    # مسارات تسجيل الدخول (JWT)
    path('api/token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # مسار الإحصائيات (Dashboard)
    path('api/dashboard/', DashboardStatsView.as_view(), name='dashboard_stats'),
    
    # مسار إعدادات الوكالة (Agency Settings)
    path('api/agency/settings/', AgencySettingsView.as_view(), name='agency_settings'),

    # Suivi GPS (proxy Traccar)
    path('api/gps/positions/', GpsPositionsView.as_view(), name='gps_positions'),
    path('api/gps/positions/<int:pk>/', GpsVehiclePositionView.as_view(), name='gps_position_detail'),
    path('api/gps/devices/', GpsDevicesView.as_view(), name='gps_devices'),
    path('api/gps/history/', GpsHistoryView.as_view(), name='gps_history'),
]

# Servir les images (MEDIA) en mode développement / lorsque le stockage local est utilisé
if settings.DEBUG and hasattr(settings, 'MEDIA_URL'):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)