from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Vehicle

# 1. تسجيل جدول الوكالات
admin.site.register(Vehicle)
