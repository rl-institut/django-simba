# Toolchain

Das Web-Tool basiert auf einer Reihe von Modulen die nacheinander ausgeführt werden. Verschiedene Module sind optional und unterstützen den Nutzer durch Aufbereitung, Beschaffung und Visualisierung. Die Module werden im Folgenden beschrieben.

## Datenbank
Die Datenbank umfasst eine Reihe an Modellen, welche verschiedene Aspekte und Komponenten eines Busbetriebs abbilden. Das Modell "VehicleType" beschreibt die Eigenschaften eines Bustyps, z.B. mit seinen technischen Eigenschaften wie Batteriekapazität, Gewicht oder Maßen. Die "Route" beschreibt einen Fahrweg der während eines Umlaufs abgefahren wird. Andere Modelle unterstützen die Nutzer in dem sie Informationen über den Ablauf der Simulation speichern, z.B. den Fortschritt der Simulation oder Meldungen über Fehler oder Anpassungen die während der Simulation vorgenommen wurden.

### Szenario
Das Modell 'Scenario' bündelt Datenpakete und speichert verschiedene Parameter, welche für die Simulation notwendig sind. WeBus enthält 3 verschiedene Szenariotypen.
 - Source / Quellszenario
 - Mutation / Veränderungsszenario
 - Simulation / Simulationsszenario

Hierbei existieren die ersten beiden Typen, um dem Nutzer ein möglichst einfachen Umgang mit Szenarien zu ermöglichen ohne wiederholt die gleichen Dateien hochladen zu müssen und die Möglichkeit zu besitzen Einstellungen aus vorherigen Simulationen zu übernehmen. Weitere Daten, die benötigt werden, um ein Szenario zu beschreiben besitzen eine Referenz zu einer Szenarioinstanz.
Für den Kern der Simulation ist nur das Simulationsszenario von Bedeutung. Dieses wird aus dem Quellszenario und dem Veränderungsszenario generiert. Im Folgenden werden nur Modelle des Simulationsszenarios beschrieben. Beschreibungen für Modelle, welche periphere Aufgaben übernehmen, wie die bereits genannte Speicherung von Simulationsfortschitt oder die Verknüpfung von Quell- und Veränderungsszenario können [hier](references.md#foo) gefunden werden

### Umlauf
Das Modell 'Rotation' / Umlauf bündelt Daten, welche einen Umlauf beschreiben. Dazu gehört der Fahrzeugtyp, ein Fahrzeug diesen Typs und ein Name. Weiter Daten die diesen Umlauf beschreiben, referenzieren eine Umlaufinstanz.

### Fahrt
Das Modell 'Trip' / Fahrt beschreibt, die Fahrt von einer Haltestelle zu einer anderen.

## Wizard

## Verbrauchsberechnung

## Stations-Optimierung

## Depotoptimierung

## Konsolidierung
