#!/usr/bin/env python3
import os
import re
from datetime import datetime
from pathlib import Path

# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "backend_report.md"

# Dossiers/fichiers à ignorer
IGNORE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "static",
    "staticfiles",
    "media",
    "migrations",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
}

IGNORE_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "db.sqlite3",
    "backend_report.md",
}

# Extensions intéressantes pour l'analyse
CODE_EXTENSIONS = {
    ".py",
    ".toml",
    ".ini",
    ".cfg",
    ".yaml",
    ".yml",
    ".json",
    ".txt",
}

MAX_FILE_SIZE = 500_000  # 500 KB


# ============================================================
# HELPERS
# ============================================================


def should_ignore(path: Path):
    """Détermine si un fichier/dossier doit être ignoré."""

    if path.name in IGNORE_FILES:
        return True

    for part in path.parts:
        if part in IGNORE_DIRS:
            return True

    return False


def relative(path: Path):
    return path.relative_to(ROOT).as_posix()


def read_file(path: Path):
    """Lit un fichier texte sans planter sur les encodages."""

    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return (
                f"# Fichier trop volumineux\n\n" f"Taille: {path.stat().st_size} bytes"
            )

        return path.read_text(encoding="utf-8", errors="replace")

    except Exception as e:
        return f"# Impossible de lire le fichier\n\nErreur: {e}"


def code_block(content, language="python"):
    return f"\n```{language}\n{content}\n```\n"


# ============================================================
# PROJECT TREE
# ============================================================


def generate_tree():
    lines = []

    def walk(directory: Path, prefix=""):
        try:
            entries = sorted(
                [p for p in directory.iterdir() if not should_ignore(p)],
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except PermissionError:
            return

        for index, path in enumerate(entries):
            is_last = index == len(entries) - 1

            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + path.name)

            if path.is_dir():
                extension = "    " if is_last else "│   "
                walk(path, prefix + extension)

    lines.append(ROOT.name + "/")
    walk(ROOT)

    return "\n".join(lines)


# ============================================================
# DJANGO FILE DISCOVERY
# ============================================================


def discover_files():
    result = {
        "models": [],
        "serializers": [],
        "views": [],
        "urls": [],
        "permissions": [],
        "services": [],
        "tasks": [],
        "admin": [],
        "tests": [],
        "settings": [],
        "other_python": [],
        "dependencies": [],
    }

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue

        if should_ignore(path):
            continue

        name = path.name.lower()

        if path.suffix == ".py":
            if name == "models.py":
                result["models"].append(path)

            elif "serializer" in name:
                result["serializers"].append(path)

            elif name in {"views.py", "viewsets.py"} or "view" in name:
                result["views"].append(path)

            elif name == "urls.py" or name == "routes.py":
                result["urls"].append(path)

            elif "permission" in name:
                result["permissions"].append(path)

            elif "service" in name:
                result["services"].append(path)

            elif name == "tasks.py":
                result["tasks"].append(path)

            elif name == "admin.py":
                result["admin"].append(path)

            elif name.startswith("test") or "tests" in path.parts:
                result["tests"].append(path)

            elif name == "settings.py" or "settings" in path.parts:
                result["settings"].append(path)

            else:
                result["other_python"].append(path)

        elif name in {
            "requirements.txt",
            "requirements-dev.txt",
            "pyproject.toml",
            "poetry.lock",
            "pipfile",
            "pipfile.lock",
            "package.json",
        }:
            result["dependencies"].append(path)

    return result


# ============================================================
# SECRET SANITIZATION
# ============================================================

SECRET_PATTERNS = [
    r"(SECRET_KEY\s*=\s*)(.+)",
    r"(PASSWORD\s*=\s*)(.+)",
    r"(API_KEY\s*=\s*)(.+)",
    r"(ACCESS_TOKEN\s*=\s*)(.+)",
    r"(REFRESH_TOKEN\s*=\s*)(.+)",
    r"(TOKEN\s*=\s*)(.+)",
    r"(DATABASE_URL\s*=\s*)(.+)",
    r"(AWS_SECRET_ACCESS_KEY\s*=\s*)(.+)",
    r"(STRIPE_SECRET_KEY\s*=\s*)(.+)",
]


def sanitize_content(content):
    """Masque certaines valeurs sensibles."""

    for pattern in SECRET_PATTERNS:
        content = re.sub(pattern, r"\1***REDACTED***", content, flags=re.IGNORECASE)

    # Masque les clés JWT/API évidentes
    content = re.sub(r"(sk_live_[A-Za-z0-9]+)", "***REDACTED_STRIPE_KEY***", content)

    content = re.sub(r"(sk_test_[A-Za-z0-9]+)", "***REDACTED_STRIPE_KEY***", content)

    return content


# ============================================================
# EXTRACT DJANGO MODELS
# ============================================================


def extract_models():
    output = []

    for path in discover_files()["models"]:
        content = read_file(path)

        classes = re.findall(r"class\s+(\w+)\s*\(\s*models\.Model\s*\)\s*:", content)

        if classes:
            output.append(
                f"### `{relative(path)}`\n\n"
                f"**Models détectés:**\n\n" + "\n".join(f"- `{c}`" for c in classes)
            )

    return "\n\n".join(output)


# ============================================================
# EXTRACT API ENDPOINTS
# ============================================================


def extract_urls():
    output = []

    for path in discover_files()["urls"]:
        content = read_file(path)

        urlpatterns = re.findall(r"path\(\s*[\"']([^\"']+)", content)

        re_paths = re.findall(r"re_path\(\s*r?[\"']([^\"']+)", content)

        routes = urlpatterns + re_paths

        if routes:
            output.append(
                f"### `{relative(path)}`\n\n"
                + "\n".join(f"- `{route}`" for route in routes)
            )

    return "\n\n".join(output)


# ============================================================
# EXTRACT CLASSES / FUNCTIONS
# ============================================================


def extract_python_symbols(path):
    content = read_file(path)

    classes = re.findall(
        r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", content, flags=re.MULTILINE
    )

    functions = re.findall(
        r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)", content, flags=re.MULTILINE
    )

    return classes, functions


def generate_file_index(files):
    output = []

    for path in sorted(files):
        classes, functions = extract_python_symbols(path)

        output.append(f"### `{relative(path)}`")

        if classes:
            output.append("\n**Classes:**\n\n" + "\n".join(f"- `{c}`" for c in classes))

        if functions:
            output.append(
                "\n**Functions:**\n\n" + "\n".join(f"- `{f}()`" for f in functions)
            )

    return "\n\n".join(output)


# ============================================================
# DEPENDENCIES
# ============================================================


def generate_dependencies():
    output = []

    for path in discover_files()["dependencies"]:
        content = read_file(path)

        output.append(
            f"### `{relative(path)}`\n" + code_block(sanitize_content(content), "text")
        )

    return "\n".join(output)


# ============================================================
# COMPLETE SOURCE CODE
# ============================================================


def generate_source_code():
    sections = []

    categories = discover_files()

    ordered_categories = [
        "models",
        "serializers",
        "views",
        "urls",
        "permissions",
        "services",
        "tasks",
        "admin",
        "settings",
        "tests",
        "other_python",
    ]

    for category in ordered_categories:
        files = categories.get(category, [])

        if not files:
            continue

        sections.append(f"## {category.upper()}\n")

        for path in sorted(files):
            content = sanitize_content(read_file(path))

            sections.append(f"### `{relative(path)}`\n" + code_block(content, "python"))

    return "\n".join(sections)


# ============================================================
# MAIN REPORT
# ============================================================


def generate_report():
    files = discover_files()

    total_files = sum(
        len(value) for key, value in files.items() if isinstance(value, list)
    )

    report = []

    report.append("# Backend Architecture Report")

    report.append(
        f"\n"
        f"> Rapport généré automatiquement le "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    # --------------------------------------------------------
    # PROJECT
    # --------------------------------------------------------

    report.append("## 1. Project Overview")

    report.append(
        f"""
- **Project:** `{ROOT.name}`
- **Backend:** Django / Django REST Framework
- **Files analysés:** {total_files}
- **Root:** `{ROOT}`
"""
    )

    # --------------------------------------------------------
    # TREE
    # --------------------------------------------------------

    report.append("## 2. Project Structure")

    report.append(code_block(generate_tree(), "text"))

    # --------------------------------------------------------
    # DJANGO MODELS
    # --------------------------------------------------------

    report.append("## 3. Django Models")

    models = extract_models()

    report.append(models if models else "Aucun `models.Model` détecté.")

    # --------------------------------------------------------
    # API
    # --------------------------------------------------------

    report.append("## 4. API Routes")

    urls = extract_urls()

    report.append(urls if urls else "Aucune route Django détectée automatiquement.")

    # --------------------------------------------------------
    # SERIALIZERS
    # --------------------------------------------------------

    report.append("## 5. Serializers")

    report.append(
        generate_file_index(files["serializers"])
        if files["serializers"]
        else "Aucun serializer trouvé."
    )

    # --------------------------------------------------------
    # VIEWS
    # --------------------------------------------------------

    report.append("## 6. Views / ViewSets")

    report.append(
        generate_file_index(files["views"])
        if files["views"]
        else "Aucune view trouvée."
    )

    # --------------------------------------------------------
    # PERMISSIONS
    # --------------------------------------------------------

    report.append("## 7. Permissions / Authorization")

    report.append(
        generate_file_index(files["permissions"])
        if files["permissions"]
        else "Aucun fichier de permissions détecté."
    )

    # --------------------------------------------------------
    # SERVICES
    # --------------------------------------------------------

    report.append("## 8. Services / Business Logic")

    report.append(
        generate_file_index(files["services"])
        if files["services"]
        else "Aucun service détecté."
    )

    # --------------------------------------------------------
    # TASKS
    # --------------------------------------------------------

    report.append("## 9. Background Tasks")

    report.append(
        generate_file_index(files["tasks"])
        if files["tasks"]
        else "Aucune tâche Celery détectée."
    )

    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------

    report.append("## 10. Django Admin")

    report.append(
        generate_file_index(files["admin"])
        if files["admin"]
        else "Aucun admin.py détecté."
    )

    # --------------------------------------------------------
    # DEPENDENCIES
    # --------------------------------------------------------

    report.append("## 11. Dependencies")

    report.append(
        generate_dependencies()
        if files["dependencies"]
        else "Aucun fichier de dépendances détecté."
    )

    # --------------------------------------------------------
    # SOURCE CODE
    # --------------------------------------------------------

    report.append("## 12. Source Code")

    report.append(generate_source_code())

    # --------------------------------------------------------
    # ANALYSIS REQUEST
    # --------------------------------------------------------

    report.append(
        """
## 13. Requested Architecture Analysis

À partir de ce rapport, analyser :

### Architecture
- Structure générale du backend
- Découpage des applications Django
- Responsabilités de chaque module
- Couplage entre les modules
- Dépendances circulaires éventuelles

### Multi-tenancy
- Organisation / Agency
- Isolation des données
- Relations agency_id
- Permissions entre agences
- Accès du super admin

### Authentication
- Users
- Roles
- Permissions
- JWT / Session / Supabase Auth
- Gestion des tokens

### Booking
- Workflow de réservation
- États d'une réservation
- Disponibilité des véhicules
- Risques de double réservation
- Transactions et concurrence

### Pricing
- Calcul du prix
- Tarifs saisonniers
- Extras
- Discounts
- Taxes
- Snapshot du prix

### Fleet
- Véhicules
- Catégories
- Disponibilité
- Maintenance
- État du véhicule

### Payments
- Paiements
- Webhooks
- Refunds
- Idempotence
- Sécurité

### API
- REST conventions
- Versioning
- Pagination
- Filtering
- Permissions
- Validation
- Error handling

### Performance
- N+1 queries
- select_related
- prefetch_related
- Index PostgreSQL
- Cache Redis
- Celery

### Security
- Secrets
- CORS
- CSRF
- Authentication
- Authorization
- File uploads
- Rate limiting
- SQL injection
- IDOR
- Data isolation

### Scalability
- Architecture actuelle
- Bottlenecks
- Horizontal scaling
- Background jobs
- Database scaling

### Recommendations
Produire une architecture cible pour un SaaS de location de voitures avec :

1. Super Admin Platform
2. Agency Dashboard
3. Customer Booking Interface
4. Django REST API
5. PostgreSQL
6. Supabase Storage/Auth si pertinent
7. Redis
8. Celery
9. Multi-tenancy
10. RBAC

Identifier également :
- ce qu'il faut garder
- ce qu'il faut modifier
- ce qu'il faut supprimer
- ce qu'il faut ajouter
- les priorités de migration
"""
    )

    OUTPUT.write_text("\n".join(report), encoding="utf-8")

    print("=" * 60)
    print("Backend report generated successfully")
    print("=" * 60)
    print(f"File: {OUTPUT}")
    print(f"Files analyzed: {total_files}")
    print("=" * 60)


if __name__ == "__main__":
    generate_report()
