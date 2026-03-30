from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Agency, CustomUser

# 1. تسجيل جدول الوكالات
admin.site.register(Agency)

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