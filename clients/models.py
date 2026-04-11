from django.db import models
from agency.models import Agency # استدعاء نموذج الوكالة

class Client(models.Model):
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='clients')
    cin_passport = models.CharField(max_length=20, verbose_name="رقم البطاقة الوطنية أو الجواز")
    date_expiration_cin = models.DateField(null=True, blank=True, verbose_name="تاريخ انتهاء صلاحية الوثيقة")
    nationalite = models.CharField(max_length=50, blank=True, null=True, verbose_name="الجنسية")
    nom = models.CharField(max_length=50, verbose_name="الاسم العائلي")
    prenom = models.CharField(max_length=50, verbose_name="الاسم الشخصي")
    telephone = models.CharField(max_length=20, verbose_name="رقم الهاتف")
    email = models.EmailField(blank=True, null=True, verbose_name="البريد الإلكتروني")
    permis_conduite = models.CharField(max_length=50, unique=True, verbose_name="رقم رخصة السياقة")
    date_delivrance_permis = models.DateField(verbose_name="تاريخ تسليم رخصة السياقة", null=True, blank=True)
    adresse = models.TextField(verbose_name="العنوان")
    
    # إضافات حديثة
    liste_noire = models.BooleanField(default=False, verbose_name="في القائمة السوداء؟")
    remarques = models.TextField(blank=True, null=True, verbose_name="ملاحظات حول الزبون")
    
    # مستندات مرفقة (Scans/Images)
    scan_cin = models.ImageField(upload_to='clients_documents/cin/', null=True, blank=True, verbose_name="صورة البطاقة الوطنية/الجواز")
    scan_permis = models.ImageField(upload_to='clients_documents/permis/', null=True, blank=True, verbose_name="صورة رخصة السياقة")
    class Meta:
        # لضمان عدم تكرار رقم البطاقة الوطنية داخل *نفس الوكالة* فقط
        unique_together = ['agency', 'cin_passport']
    def __str__(self):
        return f"{self.nom} {self.prenom} - {self.cin_passport}"