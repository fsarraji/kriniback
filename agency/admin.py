from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Agency, CustomUser, Subscription

# 1. تسجيل جدول الوكالات
admin.site.register(Agency)

# Abonnements SaaS
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('agency', 'plan', 'price', 'start_date', 'end_date', 'status')
    list_filter = ('plan', 'status')
    search_fields = ('agency__nom_agence',)
    list_select_related = ('agency',)

admin.site.register(Subscription, SubscriptionAdmin)

# 2. تخصيص واجهة المستخدم لإظهار الحقول الجديدة
class CustomUserAdmin(UserAdmin):
    # إضافة الحقول لشاشة "تعديل" المستخدم
    fieldsets = UserAdmin.fieldsets + (
        ('معلومات الوكالة والصلاحيات', {
            'fields': ('agency', 'role'),
        }),
    )
    
    # إضافة الحقول لشاشة "إضافة" مستخدم جديد من لوحة التحكم
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('معلومات الوكالة والصلاحيات', {
            'fields': ('agency', 'role'),
        }),
    )

# 3. تسجيل المستخدم المخصص مع الواجهة الجديدة
admin.site.register(CustomUser, CustomUserAdmin)