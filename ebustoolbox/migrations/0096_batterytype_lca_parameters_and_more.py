from django.db import migrations, models


class Migration(migrations.Migration):
    """Follow eflips-model 11.2.0, which added ``lca_parameters`` and retyped
    ``BatteryType.chemistry`` from JSONB to text, and give the scenario what
    eflips-impact needs: ``eta_avail`` among the TCO parameters and a column for
    the LCA result.
    """

    dependencies = [
        ("ebustoolbox", "0095_alter_station_charge_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="batterytype",
            name="lca_parameters",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="chargingpointtype",
            name="lca_parameters",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="scenario",
            name="lca_result",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="vehicletype",
            name="lca_parameters",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    # Only a JSON string holds a chemistry. The column used to default to an
                    # object, which has no text form worth keeping.
                    sql="""
                        ALTER TABLE "BatteryType"
                        ALTER COLUMN "chemistry" TYPE text
                        USING CASE
                            WHEN jsonb_typeof("chemistry") = 'string' THEN "chemistry" #>> '{}'
                            ELSE ''
                        END;
                    """,
                    reverse_sql="""
                        ALTER TABLE "BatteryType"
                        ALTER COLUMN "chemistry" TYPE jsonb
                        USING to_jsonb("chemistry");
                    """,
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="batterytype",
                    name="chemistry",
                    field=models.TextField(blank=True, default=""),
                ),
            ],
        ),
        migrations.AlterField(
            model_name="scenario",
            name="tco_parameters",
            field=models.JSONField(
                blank=True,
                db_default={
                    "cost_escalation_rate": {
                        "diesel": 0.02,
                        "electricity": 0.02,
                        "general": 0.02,
                        "insurance": 0.02,
                        "staff": 0.02,
                    },
                    "eta_avail": 1.0,
                    "fuel_cost": {"diesel": 1.5, "electricity": 0.18},
                    "inflation_rate": 0.02,
                    "infra_maint_cost": 1000.0,
                    "insurance": 0.0,
                    "interest_rate": 0.04,
                    "project_duration": 20,
                    "staff_cost": 30.0,
                    "taxes": 0.0,
                    "vehicle_maint_cost": {"diesel": 0.14, "electricity": 0.07},
                },
                null=True,
            ),
        ),
    ]
