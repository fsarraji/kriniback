from django.db import models
from agency.models import Agency # استدعاء نموذج الوكالة


class Brand(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name="اسم العلامة التجارية")
    
    def __str__(self):
        return self.name

class ModelCar(models.Model):
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name='models', verbose_name="العلامة التجارية")
    name = models.CharField(max_length=100, verbose_name="اسم الموديل")
    
    class Meta:
        unique_together = ('brand', 'name')
        verbose_name = "موديل السيارة"
        verbose_name_plural = "موديلات السيارات"

    def __str__(self):
        return f"{self.brand.name} {self.name}"


class Vehicle(models.Model):
    FUEL_CHOICES =[
        ('Diesel', 'ديزل'),
        ('Essence', 'بنزين'),
        ('Hybride', 'هجين'),
        ('Electrique', 'كهربائي'),
    ]
    
    STATUS_CHOICES =[
        ('Available', 'متاحة'),
        ('Rented', 'مكتراة'),
        ('Maintenance', 'في الصيانة'),
    ]
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='vehicles')
    matricule = models.CharField(max_length=20, unique=True, verbose_name="رقم الترقيم") # مثال: 12345-A-50
    marque = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, verbose_name="العلامة التجارية") 
    modele = models.ForeignKey(ModelCar, on_delete=models.SET_NULL, null=True, verbose_name="الموديل")
    annee = models.IntegerField(verbose_name="سنة الصنع")
    couleur = models.CharField(max_length=30, verbose_name="اللون")
    carburant = models.CharField(max_length=20, choices=FUEL_CHOICES, verbose_name="نوع الوقود")
    kilometrage = models.IntegerField(verbose_name="الكيلومترات الحالية")
    prix_par_jour = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="ثمن الكراء اليومي")
    chauffeur_disponible = models.BooleanField(default=False, verbose_name="سائق متوفر")
    statut = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Available', verbose_name="حالة السيارة")
    
    # التواريخ الهامة للتنبيهات (التي كانت تظهر بالأحمر والأخضر في الفيديو)
    date_assurance = models.DateField(verbose_name="تاريخ انتهاء التأمين")
    date_visite_technique = models.DateField(verbose_name="تاريخ انتهاء الفحص التقني")
    prochain_vidange_km = models.IntegerField(verbose_name="الكيلومتراج القادم لتغيير الزيت")
    
    # صورة السيارة
    image = models.ImageField(upload_to='vehicles_images/', null=True, blank=True)

    def __str__(self):
        return f"{self.marque} {self.modele} - {self.matricule}"