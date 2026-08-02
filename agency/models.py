from django.db import models
from django.contrib.auth.models import AbstractUser

# 1. Modèle de l'agence (Société)
class Agency(models.Model):
    nom_agence = models.CharField(max_length=100, verbose_name="Nom de l'agence")
    adresse = models.TextField(verbose_name="Adresse")
    telephone = models.CharField(max_length=20, verbose_name="Téléphone")
    rc = models.CharField(max_length=50, blank=True, null=True, verbose_name="Registre du Commerce (RC)")
    ice = models.CharField(max_length=50, blank=True, null=True, verbose_name="Identifiant Commun de l'Entreprise (ICE)")
    email = models.EmailField(blank=True, null=True)
    logo = models.ImageField(upload_to='agency_logos/', null=True, blank=True)
    cachet_signature = models.ImageField(upload_to='agency_cachets/', null=True, blank=True, verbose_name="Cachet et Signature")
    date_creation = models.DateTimeField(auto_now_add=True)
    
    is_active = models.BooleanField(default=True, verbose_name="Compte actif")
    caution_active = models.BooleanField(default=True, verbose_name="Caution active")
    caution_montant = models.DecimalField(max_digits=10, decimal_places=2, default=1500.00, verbose_name="Montant de la caution")

    # Kilométrage journalier inclus
    km_extra_active = models.BooleanField(default=True, verbose_name="Facturation km extra active")
    km_par_jour = models.IntegerField(default=250, verbose_name="Km inclus par jour")
    km_tarif_extra_defaut = models.DecimalField(max_digits=6, decimal_places=2, default=1.50, verbose_name="Tarif DH par km supplémentaire (défaut)")

    def __str__(self):
        return self.nom_agence

# 2. نموذج المستخدم المخصص المرتبط بالوكالة
class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('OWNER', 'Gérant / Propriétaire'),
        ('EMPLOYEE', 'Employé'),
        ('CLIENT', 'Client'),
    )
    
    # link user to agency
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='users', null=True, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='EMPLOYEE', verbose_name="Rôle")

    def __str__(self):
        return f"{self.username} - {self.agency.nom_agence if self.agency else 'Super Admin'}"