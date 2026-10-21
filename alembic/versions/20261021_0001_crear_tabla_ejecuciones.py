"""Crea la tabla de ejecuciones.

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ejecuciones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("estado", sa.String(32), nullable=False),
        sa.Column("paso", sa.String(32), nullable=True),
        sa.Column("origen", sa.String(32), nullable=False),
        sa.Column("escenario", sa.String(32), nullable=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalizada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("desde", sa.DateTime(timezone=True), nullable=True),
        sa.Column("marca_agua", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registros_leidos", sa.Integer(), nullable=False),
        sa.Column("registros_exportados", sa.Integer(), nullable=False),
        sa.Column("registros_invalidos", sa.Integer(), nullable=False),
        sa.Column("registros_duplicados", sa.Integer(), nullable=False),
        sa.Column("nombre_fichero", sa.String(255), nullable=True),
        sa.Column("sha256_fichero", sa.String(64), nullable=True),
        sa.Column("email_enviado", sa.Boolean(), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=True),
        sa.Column("codigo_error", sa.String(32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("lease_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ejecuciones"),
        sa.CheckConstraint(
            "estado IN ('pending', 'running', 'completed', 'failed')",
            name="ck_ejecuciones_estado",
        ),
        sa.CheckConstraint(
            "paso IN ('fetching_source', 'writing_file', 'sending_email', 'email_sent')",
            name="ck_ejecuciones_paso",
        ),
        sa.CheckConstraint("origen IN ('manual', 'scheduled')", name="ck_ejecuciones_origen"),
        sa.CheckConstraint("escenario IN ('temporary_error', 'slow')", name="ck_ejecuciones_escenario"),
        sa.CheckConstraint(
            "codigo_error IN ('source_unavailable', 'source_contract_violation', 'email_send_failed', "
            "'email_delivery_unknown', 'worker_lost', 'unexpected_error')",
            name="ck_ejecuciones_codigo_error",
        ),
    )
    op.create_index("ix_ejecuciones_creada_en", "ejecuciones", ["creada_en"])
    # Como mucho una ejecución activa: índice único sobre una constante, solo para pending/running.
    op.execute(
        "CREATE UNIQUE INDEX ux_ejecuciones_una_activa ON ejecuciones ((true)) WHERE estado IN ('pending', 'running')",
    )


def downgrade() -> None:
    op.drop_table("ejecuciones")
