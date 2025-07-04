# Toolchain
+code
Das Web-Tool basiert auf einer Reihe von Modulen die nacheinander ausgeführt werden. Verschiedene Module sind optional und unterstützen den Nutzer durch Aufbereitung, Beschaffung und Visualisierung. Die Module werden im Folgenden beschrieben.

## Datenbank

### Szenario
Das Modell `Scenario` bündelt Datenpakete und speichert verschiedene Parameter, welche für die Simulation notwendig sind. WeBus enthält 3 verschiedene Szenariotypen.
 - Source / Quellszenario
 - Mutation / Veränderungsszenario
 - Simulation / Simulationsszenario

Hierbei existieren die ersten beiden Typen, um dem Nutzer ein möglichst einfachen Umgang mit Szenarien zu ermöglichen ohne wiederholt die gleichen Dateien hochladen zu müssen und die Möglichkeit zu besitzen Einstellungen aus vorherigen Simulationen zu übernehmen. Weitere Daten, die benötigt werden, um ein Szenario zu beschreiben besitzen eine Referenz zu einer Szenarioinstanz.
Für den Kern der Simulation ist nur das Simulationsszenario von Bedeutung. Dieses wird aus dem Quellszenario und dem Veränderungsszenario generiert. Im Folgenden werden nur Modelle des Simulationsszenarios beschrieben. Beschreibungen für Modelle, welche periphere Aufgaben übernehmen, wie die bereits genannte Speicherung von Simulationsfortschitt oder die Verknüpfung von Quell- und Veränderungsszenario können [hier](references.md#ebustoolbox.models) gefunden werden.

[Details zur Implementierung](references.md#ebustoolbox.models.Scenario)

### Umlauf
Das Modell `Rotation` / Umlauf bündelt Daten, welche einen Umlauf beschreiben. Dazu gehört der Fahrzeugtyp, ein Fahrzeug diesen Typs und ein Name. Weiter Daten die diesen Umlauf beschreiben, referenzieren eine Umlaufinstanz.

[Details zur Implementierung](references.md#ebustoolbox.models.Rotation)

### Fahrt
Das Modell `Trip` / Fahrt beschreibt, die Fahrt von einer Haltestelle zu einer anderen.

[Details zur Implementierung](references.md#ebustoolbox.models.Trip)

## Fahrzeug

Das Modell `Vehicle` repräsentiert ein Fahrzeug in einem Szenario. Die technischen Eigenschaften werden über einen Fremdschlüssel in einem weiteren Modell gespeichert.

[Details zur Implementierung](references.md#ebustoolbox.models.Vehicle)

## Fahrzeugtyp

Das Modell `VehicleType` beschreibt den Fahrzeugtyp, der in einem Szenario verwendet wird.
Es beinhaltet technische Eigenschaften wie die Batteriekapazität, die Ladekurve, den Verbrauch und die geometrischen Maße.
Ein Fremdschlüssel kann genutzt werden, um weitere Informationen über die Batterieeigenschaften zu definieren.


[Details zur Implementierung](references.md#ebustoolbox.models.VehicleType)

## Fahrzeugklasse

Das Modell `VehicleClass` beschreibt eine Fahrzeugklasse, die mit einem Szenario verbunden ist.
Das Modell erlaubt die Verknüpfung von erweiterten Eigenschaften die mehreren Fahrzeugtypen zugeordnet sein können.
Die Fahrzeugklasse wird zum Beispiel genutzt um einem Fahrzeugtyp einen detailierten [Verbrauch](#verbrauch) zuzuordnen.

[Details zur Implementierung](references.md#ebustoolbox.models.VehicleClass)


<!-- # 1.1 [Header](#1.1)    -->
<!---->
<!-- ...some text... -->
<!---->
<!-- # 1.1 Header<a id='1.1'></a> -->

# Verbrauch

Das Modell `Consumption` beschreibt Verbrauchsdaten, die mit einem Szenario und einer Fahrzeugklasse verbunden sind.
Eine Instanz besteht aus zwei Listen.
`values` enthält Verbrauchswerte in kWh/km.
`data_points` enthält die Randbedingungen die für die eben genannten Verbräuche zu grunde liegt.
### Attribute:
- `name` (CharField): Der Name der Verbrauchsdaten. Muss eindeutig für das Szenario sein.
- `vehicle_class` (ForeignKey): Die Fahrzeugklasse, mit der die Verbrauchsdaten verbunden sind.
- `scenario` (ForeignKey): Das Szenario, dem die Verbrauchsdaten zugeordnet sind.
- `columns` (JSONField): Eine Liste von Spaltennamen, die die Eingangsbedingungen für die Verbrauchsberechnung darstellen.
- `data_points` (ArrayField): Eine Liste von Eingabewerten für die Verbrauchsberechnung.
- `values` (ArrayField): Eine Liste von Verbrauchswerten, die den Datenpunkten in kWh/km entsprechen.

### Meta:
- `db_table`: Der Name der Datenbanktabelle für dieses Modell (auf "ConsumptionLut" gesetzt).

### Methoden:
- `to_simba_name()`: Erstellt einen eindeutigen Namen für SimBA.
- `get_id_from_simba_name(name)`: Gibt die ID eines SimBA-Namens zurück.
- `to_df()`: Konvertiert die Verbrauchsdaten in ein Pandas DataFrame.
- `from_df(df)`: Erstellt ein `Consumption`-Objekt aus einem Pandas DataFrame.

## Station

Das Modell `Station` repräsentiert eine Station, die mit einem Szenario verbunden ist.

### Attribute:
- `geom` (PointField): Der geografische Punkt, der die Position der Station ohne Höhenangabe beschreibt. Verwendet SRID 4326 für geografische Koordinaten.
- `name` (TextField): Der Name der Station. Kann nicht null sein.
- `name_short` (TextField, optional): Ein kurzer Name für die Station. Kann leer sein.
- `scenario` (ForeignKey): Das Szenario, dem die Station zugeordnet ist.
- `is_electrified` (BooleanField): Gibt an, ob die Station elektrifiziert ist. Standardmäßig auf False gesetzt.
- `is_electrifiable` (BooleanField): Gibt an, ob die Station elektrifiziert werden kann. Standardmäßig auf True gesetzt.

### Meta:
- `db_table`: Der Name der Datenbanktabelle für dieses Modell (auf "Station" gesetzt).

## Line

Das Modell `Line` beschreibt eine Linie, die mit einem Szenario verbunden ist.

### Attribute:
- `scenario` (ForeignKey): Das Szenario, dem die Linie zugeordnet ist.
- `name` (TextField): Der Name der Linie. Kann nicht null oder leer sein.
- `name_short` (TextField, optional): Ein kurzer Name für die Linie.

### Meta:
- `db_table`: Der Name der Datenbanktabelle für dieses Modell (auf "Line" gesetzt).

## Route

Das Modell `Route` beschreibt eine Route, die mit einem Szenario verbunden ist.

### Attribute:
- `geom` (LineStringField): Die Form der Route mit Höhenangaben. Verwendet SRID 4326 für geografische Koordinaten.
- `distance` (FloatField): Die zurückgelegte Strecke der Route in Metern.
- `name` (TextField, optional): Der Name der Route. Kann null sein.
- `name_short` (TextField, optional): Ein kurzer Name für die Route. Kann leer sein.
- `scenario` (ForeignKey): Das Szenario, dem die Route zugeordnet ist.
- `line` (ForeignKey, optional): Die Linie, die mit der Route verbunden ist.
- `departure_station` (ForeignKey): Die Abfahrtsstation der Route.
- `arrival_station` (ForeignKey): Die Ankunftsstation der Route.

### Meta:
- `db_table`: Der Name der Datenbanktabelle für dieses Modell (auf "Route" gesetzt).

## Depot

Das Modell `Depot` repräsentiert einen Depotstandort, an dem Fahrzeuge geparkt, gewartet und dispatched werden.

### Attribute:
- `scenario` (ForeignKey): Das Szenario, dem das Depot zugeordnet ist.
- `name` (TextField): Der Name des Depots. Kann nicht null sein.
- `location` (PointField): Die geografische Lage des Depots.

### Meta:
- `db_table`: Der Name der Datenbanktabelle für dieses Modell (auf "Depot" gesetzt).

## Plan

Das Modell `Plan` beschreibt den Planungsprozess von Fahrzeugumläufen.

### Attribute:
- `scenario` (ForeignKey): Das Szenario, dem der Plan zugeordnet ist.
- `name` (TextField): Der Name des Plans.
- `routes` (ManyToManyField): Eine Liste von Routen, die im Plan enthalten sind.

### Meta:
- `db_table`: Der Name der Datenbanktabelle für dieses Modell (auf "Plan" gesetzt).

## Process

Das Modell `Process` beschreibt einen Prozess innerhalb des Szenarios.

### Attribute:
- `scenario` (ForeignKey): Das Szenario, dem der Prozess zugeordnet ist.
- `name` (TextField): Der Name des Prozesses.
- `status` (BooleanField): Der Status des Prozesses (z.B., ob er abgeschlossen ist).

### Meta:
- `db_table`: Der Name der Datenbanktabelle für dieses Modell (auf "Process" gesetzt).

## Area

Das Modell `Area` beschreibt ein geografisches Gebiet innerhalb eines Szenarios.

### Attribute:
- `name` (TextField): Der Name des Gebiets.
- `boundary` (PolygonField): Die geographische Grenze des Gebiets.
- `scenario` (ForeignKey): Das Szenario, dem das Gebiet zugeordnet ist.

### Meta:
- `db_table`: Der Name der Datenbanktabelle für dieses Modell (auf "Area" gesetzt).

## Wizard

## Verbrauchsberechnung

## Stations-Optimierung

## Depotoptimierung

## Konsolidierung
