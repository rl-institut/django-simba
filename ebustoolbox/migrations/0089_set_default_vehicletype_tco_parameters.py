"""
Data migration: populate tco_parameters for the built-in default vehicle types.

Source: PwC E-Bus Radar 2025
  https://www.pwc.de/de/branchen-und-markte/oeffentlicher-sektor/pwc-e-bus-radar-2025.pdf

  Electric:  Solobus 580k, Gelenkbus 780k, Midibus 10/12×Solobus, Minibus 200k
  Diesel:    Solobus 260k, Gelenkbus 360k, Midibus 10/12×diesel-Solobus, Minibus 100k
  Zusatzheizung variants: -10 % on electric procurement_cost, same diesel cost
  useful_life=14 yr, cost_escalation=0.02 for all types.
"""

from django.db import migrations

# fmt: off
#                              electric   diesel
_COSTS = {
    "Solobus":                  (580_000,  260_000),
    "Solobus_Zusatzheizung":    (522_000,  234_000),
    "Gelenkbus":                (780_000,  360_000),
    "Gelenkbus_Zusatzheizung":  (702_000,  324_000),
    "Midibus":                  (483_333,  216_667),
    "Midibus_Zusatzheizung":    (435_000,  195_000),
    "Minibus":                  (200_000,  100_000),
    "Minibus_Zusatzheizung":    (180_000,   90_000),
}
# fmt: on

_DEFAULT_TCO = {
    "useful_life": 14,
    "cost_escalation": 0.02,
}


def populate_tco_parameters(apps, schema_editor):
    VehicleType = apps.get_model("ebustoolbox", "VehicleType")
    DefaultScenario = apps.get_model("ebustoolbox", "DefaultScenario")

    ds = DefaultScenario.objects.first()
    if ds is None:
        return

    for vt in VehicleType.objects.filter(scenario=ds.scenario):
        electric_cost, diesel_cost = _COSTS.get(vt.name, (550_000, 250_000))
        p = vt.tco_parameters or {}
        p.update({
            **_DEFAULT_TCO,
            "procurement_cost": electric_cost,
            "procurement_cost_diesel": diesel_cost,
        })
        vt.tco_parameters = p
        vt.save(update_fields=["tco_parameters"])


def reverse_populate(apps, schema_editor):
    # Not reversible — leave data as-is on rollback.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("ebustoolbox", "0088_vehicletype_energy_source"),
    ]

    operations = [
        migrations.RunPython(populate_tco_parameters, reverse_populate),
    ]
