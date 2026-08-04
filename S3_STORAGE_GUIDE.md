# Guide : Activer Supabase Storage (S3) pour les images

## Pourquoi

Les images (`vehicles_images/`, scans clients, cachets d'agence) sont stockées **localement** sur le disque de Render, qui est **éphémère** : tout fichier disparaît à chaque redéploiement. Résultat : les URL d'images renvoient `404` avec du `text/html` → le frontend web bloque la lecture (erreur CORB dans la console) et les images n'apparaissent plus.

L'activation du S3 est déjà prévue dans le code (`car_rental_backend/settings.py`) : il suffit que les variables d'environnement `AWS_*` soient présentes sur Render.

## Étapes

### 1. Créer un bucket public dans Supabase Storage

1. Console Supabase → votre projet (celui du backend).
2. **Storage** → **New bucket** :
   - Nom : par ex. `krinimediabucket`
   - **Public bucket : ON** (indispensable pour des URL publiques sans signature)
3. **Storage → Policies** → créer une politique `SELECT` **public** sur ce bucket (Allow all, pour lecture publique).

### 2. Récupérer les clés S3 (compatibles)

1. Console Supabase → **Project Settings** → **API** (ou **Storage**).
2. Onglet **S3 Access Keys** → **Create new key**.
   - `Access Key ID`
   - `Secret Access Key`
3. Endpoint : `https://<PROJECT_REF>.supabase.co/storage/v1/s3`
   - Exemple avec le ref visible dans l'URL du projet : `https://gojkevxwwoimalftpuzg.supabase.co/storage/v1/s3`

### 3. Configurer les variables d'environnement sur Render

Service web du backend → **Environment** → ajouter :

| Variable | Valeur |
|---|---|
| `AWS_ACCESS_KEY_ID` | `<Access Key ID S3>` |
| `AWS_SECRET_ACCESS_KEY` | `<Secret Access Key S3>` |
| `AWS_STORAGE_BUCKET_NAME` | `krinimediabucket` |
| `AWS_S3_ENDPOINT_URL` | `https://<PROJECT_REF>.supabase.co/storage/v1/s3` |
| `AWS_S3_REGION_NAME` | `eu-central-1` |

⚠️ **Redéploiement nécessaire** : le serveur doit relire ces variables pour basculer sur le stockage S3.

### 4. Vérifier que le stockage S3 est actif

Après déploiement, lancer la commande de diagnostic incluse dans le repo :

```
python manage.py check_storage
```

Attendu :
- `Backend actif : storages.backends.s3boto3.S3Boto3Storage`
- `Variables S3/Supabase définies : OUI`
- `=> Stockage actif : SUPABASE STORAGE (S3). Les images persisteront.`

### 5. Re-uploader les images existantes

Les anciennes images sont perdues (disque éphémère). Il faut ré-uploader les photos via l'app (écran véhicule, scan documents, réglages agence) après la bascule. Les nouvelles images seront alors servies directement depuis Supabase avec le bon `Content-Type` d'image (plus de 404 HTML, plus de CORB).

## Commentaires de code pertinents

- Bascule S3 : `car_rental_backend/settings.py` (bloc `if AWS_ACCESS_KEY_ID and ...`)
- Diagnostic : `fleet/management/commands/check_storage.py`
- URL des images côté frontend : `src/apiUrl.js` (`resolveMediaUrl`)
