"""Profil international : pays, locale, fuseau horaire et devise d'affichage.

Revision ID: 0002_international_profile
Revises: 0001_initial
Create Date: 2026-10-19

Contexte
--------
L'application ne demande ni ne stocke de **nationalité** : les quatre colonnes
ajoutées servent uniquement à l'affichage et à la normalisation des saisies :

* `country_code` : code ISO 3166-1 alpha-2 (affichage, interprétation d'un
  numéro de téléphone local) ;
* `locale`      : locale BCP 47 (format des nombres et des dates) ;
* `timezone`    : fuseau IANA (heures habituelles, affichage des horodatages) ;
* `currency`    : devise d'affichage ISO 4217 (les comptes restent libellés en
  EUR, devise de base du prototype ; aucune conversion n'est inventée).

Compatibilité des données existantes
------------------------------------
Les colonnes sont ajoutées avec une valeur par défaut côté serveur (`FR`,
`fr-FR`, `Europe/Paris`, `EUR`) : les utilisateurs du jeu de démonstration
français conservent exactement leur comportement d'affichage. Cette valeur par
défaut reste en place (elle protège aussi les insertions faites hors ORM).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002_international_profile"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("country_code", sa.String(length=2), nullable=False, server_default="FR")
        )
        batch_op.add_column(
            sa.Column("locale", sa.String(length=15), nullable=False, server_default="fr-FR")
        )
        batch_op.add_column(
            sa.Column("timezone", sa.String(length=50), nullable=False, server_default="Europe/Paris")
        )
        batch_op.add_column(
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="EUR")
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("currency")
        batch_op.drop_column("timezone")
        batch_op.drop_column("locale")
        batch_op.drop_column("country_code")
