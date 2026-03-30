from django.db import models
from django.contrib.auth.models import AbstractUser

# 1. نموذج الوكالة (الشركة)
class Agency(models.Model):
    nom_agence = models.CharField(max_length=100, verbose_name="اسم الوكالة")
    adresse = models.TextField(verbose_name="العنوان")
    telephone = models.CharField(max_length=20, verbose_name="الهاتف")
    rc = models.CharField(max_length=50, blank=True, null=True, verbose_name="الرقم التجاري (RC)")
    ice = models.CharField(max_length=50, blank=True, null=True, verbose_name="رقم التعريف الموحد (ICE)")
    email = models.EmailField(blank=True, null=True)
    logo = models.ImageField(upload_to='agency_logos/', null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    
    # يمكنك لاحقاً إضافة حقول مثل: حالة الاشتراك، تاريخ انتهاء الاشتراك، الخ.
    is_active = models.BooleanField(default=True, verbose_name="حساب الوكالة نشط")

    def __str__(self):
        return self.nom_agence

# 2. نموذج المستخدم المخصص المرتبط بالوكالة
class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('OWNER', 'مدير/مالك الوكالة'),
        ('EMPLOYEE', 'موظف'),
    )
    
    # ربط المستخدم بالوكالة (null=True حتى نتمكن من إنشاء Superuser للنظام ككل)
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='users', null=True, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='EMPLOYEE', verbose_name="الدور")

    def __str__(self):
        return f"{self.username} - {self.agency.nom_agence if self.agency else 'Super Admin'}"