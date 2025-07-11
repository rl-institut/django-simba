# Toolchain
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

### Fahrzeug

Das Modell `Vehicle` repräsentiert ein Fahrzeug in einem Szenario. Die technischen Eigenschaften werden über einen Fremdschlüssel in einem weiteren Modell gespeichert.

[Details zur Implementierung](references.md#ebustoolbox.models.Vehicle)

### Fahrzeugtyp

Das Modell `VehicleType` beschreibt den Fahrzeugtyp, der in einem Szenario verwendet wird.
Es beinhaltet technische Eigenschaften wie die Batteriekapazität, die Ladekurve, den Verbrauch und die geometrischen Maße.
Ein Fremdschlüssel kann genutzt werden, um weitere Informationen über die Batterieeigenschaften zu definieren.


[Details zur Implementierung](references.md#ebustoolbox.models.VehicleType)

### Fahrzeugklasse

Das Modell `VehicleClass` beschreibt eine Fahrzeugklasse, die mit einem Szenario verbunden ist.
Das Modell erlaubt die Verknüpfung von erweiterten Eigenschaften die mehreren Fahrzeugtypen zugeordnet sein können.
Die Fahrzeugklasse wird zum Beispiel genutzt um einem Fahrzeugtyp einen detailierten [Verbrauch](#verbrauch) zuzuordnen.

[Details zur Implementierung](references.md#ebustoolbox.models.VehicleClass)


<!-- # 1.1 [Header](#1.1)    -->
<!---->
<!-- ...some text... -->
<!---->
<!-- # 1.1 Header<a id='1.1'></a> -->

### Verbrauch

Das Modell `Consumption` beschreibt Verbrauchsdaten, die mit einem Szenario und einer Fahrzeugklasse verbunden sind.
Eine Instanz besteht aus zwei Listen aus Datenpunkten.
`values` enthält Verbrauchswerte in kWh/km.
`data_points` enthält die Randbedingungen die für die eben genannten Verbräuche zu Grunde liegt. Sie müssen für jeder Zeile der Liste `values` vollständig vorliegen und eine Liste folgender Werte enthalten:
- Steigung [m/m]
- Beladungszustand zwischen 0 und 1 [-]
- Geschwindigkeit [km/h]
- Umgebungstemperatur [˚C]

Die Reihenfolge der Werte wird über das Feld `columns` beschrieben. Diese Liste enthält die Einträge:

- incline
- t_amb
- level_of_loading
- mean_speed_kmh
- consumption_kwh_per_km

Der Eintrag `vehicle_class` wird genutzt um die Gruppe an Fahrzeugtypen zu referenzieren die diese Verbrauchs-Instanz nutze wird genutzt um die Gruppe an Fahrzeugtypen zu referenzieren die diese Verbrauchs-Instanz nutzen.

[Details zur Implementierung](references.md#ebustoolbox.models.Consumption)

## Station

Das Modell `Station` repräsentiert eine Haltestelle, ein Depot oder eine andere Position an der Fahrten planmäßig starten oder Enden.
Die `Station` umfasst vor Allem Spezifikationen zur Elektrifizierung, z.B. über das Spannungsniveau, Anzahl der Ladepunkte oder die mögliche Ladeleistung.
Die Station muss nicht zwangsweise elektrifiziert sein.
Wenn die Elektrifizierung nicht möglich sein, so kann das Attribut `is_electrifiable` auf `False` gesetzt werden.
Für die Kartendarstellung kann außerdem die Position als Geokoordinate hinterlegt werden.

[Details zur Implementierung](references.md#ebustoolbox.models.Station)

## Line

Das Modell `Line` beschreibt eine Linie, die mit einem Szenario verbunden ist.
`Route` Objekte können einen Fremdschlüssel besitzen der auf ein `Line` Objekt verweist.
Die Route bedient also diese Linie.

[Details zur Implementierung](references.md#ebustoolbox.models.Line)

## Route

Das Modell `Route` beschreibt eine Route, die mit einem Szenario verbunden ist. Die Form der Route kann optional als LineStringField im SRID 4326 Format angegeben werden. Zwingend erforderlich ist das Feld `distance`, welches die Distanz der Route in Metern angibt.
Ein `Route` Objekt kann einen Fremdschlüssel auf eine Line besitzen. Dies bedeutet das die Route die Line bedient.
Jede Route muss eine Abfahrts- und Ankunftsstation als Fremdschlüssel zu einer `Station` besitzen.
Eine `Route` kann optional über eine ManyToMany Verknüpfung auf weitere Stationen verweisen, welche innerhalb der Route angefahren werden.
Dies erlaubt eine genauere Beschreibung des vollzogenen Wegs, wird für die Simulation allerdings nicht benötigt.

[Details zur Implementierung](references.md#ebustoolbox.models.Route)

## Depot

Das Modell `Depot` repräsentiert einen Depotstandort, an dem Fahrzeuge geparkt, gewartet und dispatched werden.
Ein `Depot` benötigt einen Fremdschlüssel auf eine `Station` und einen `Plan`.
Optional kann eine bounding box als PolygonField

[Details zur Implementierung](references.md#ebustoolbox.models.Depot)

## Plan

Das Modell `Plan` beschreibt eine Reihenfolge an Prozessen, welche mit Fahrzeugen in Depots ausgeführt werden. Der `Plan` ist mit einem Szenario verbunden und enthält eine ManyToMany Verbindung mit `Process` Objekten

[Details zur Implementierung](references.md#ebustoolbox.models.Plan)

## Process

Das Modell `Process` beschreibt einen Aktion die mit Fahrzeugen in einem Depot ausgeführt wird.

[Details zur Implementierung](references.md#ebustoolbox.models.Process)

## Area

Das Modell `Area` beschreibt ein Gebiet innerhalb eines Depots, welches für die Durchführung eines `Process` notwendig ist.

[Details zur Implementierung](references.md#ebustoolbox.models.Area)

## Temperatures

Die Verbrauchsmodellierung mittels optionaler `Consumption` benötigt Temperaturen.
`Temperatures` Objekte benötigen zwei listen zur Erstellung.
Eine Liste von DateTimes, welche die Zeitpunkte der Messung definiert und eine liste aus Temperaturen.
Der Index der Temperatur entspricht hierbei dem Index der Messung.
Das Modell unterstützt zwei Formen, der Temperatureingabe über den booleschen wert `use_only_time`.
Wenn dieser Wert `True` ist, so wird nur ein einziger Tag an Temperaturwerten benötigt.
Die Temperatur ist mit dieser Einstellung nicht abhängig vom Datum, sondern lediglich von der Uhrzeit.
Bei dieser Einstellung dürfen die Temperaturdaten nur einen einzigen Tag umfassen.
Wenn eine Temperatur in Abhängigkeit des Datums für die Simulation genutzt werden soll, muss `use_only_time = False` gesetzt werden.
Die Temperaturdaten müssen in diesem Fall den gesamten Simulationszeitraum umfassen.
Sollte für die Simulation eine Temperatur für einen nicht hinterlegten Zeitpunkt benötigt werden, wird dieser Wert über lineare Interpolation ermittelt.

## Event

Das Modell `Event` speichert Daten die während der Simulation erzeugt wurden.
Diese Daten beschreiben den Zustand der Fahrzeuge zu verschiedenen Zeitpunkten.
Sie sind einer `Station`, einem `Trip` oder einer `Area` zugeordnet.
Jedes Event enthält den SoC zu Beginn und am Ende des Events. Optional können weitere Zeitreihen oder Beschreibungen hinzugefügt werden.

[Details zur Implementierung](references.md#ebustoolbox.models.Event)


# Erstellung eines Szenarios
Für die Simulation eines Szenarios, muss ein Szenario in der Datenbank erstellt werden.
Dies geschieht über die ScheduleReader und den Wizard der Website Oberfläche.
Schritt für Schritt gibt der User Daten ein, welche für die Simulation notwendig sind. Der erste Schritt besteht hierbei beim einlesen einer Datei.
Das einlesen einer Datei
WeBus unterstützt folgende Dateiformate.

- x10
- SimBA .csv Format
- GTFS

Szenarien können jedoch auch manuell erzeugt werden. Dazu sind folgende Schritte einzuhalten.
Erstellung eines `Scenario` Objekts.
Eine reihe von Einstellungen für die Simulation kann über die Einstellung simba_options und eflips_depot_options getätigt werden.
Erstellung von `VehicleType` Objekten mit den gewünschten technischen Eigenschaften.
Erstellung von Haltestellen und Depots als Objekte vom Typ `Station`.
Mindestens eine Station muss als Depot definiert werden.
```
charge_type = EnumChargeType.DEPOT
```
Außerdem muss jeder Umlauf in einem Depot starten und enden, so dass die Nachladung aller Fahrzeuge prinzipiell möglich ist. Reine Gelgenheitslader, welche nie im Depot stehen, werden durch die Simulation momentan nicht unterstützt. Wenn konstante Verbräuche für die Simulation genutzt werden, ist keine Erstellung von `Consumption` Objekten oder `VehicleClass` Objekten notwendig.
Nun können `Rotation` Objekte erzeugt werden, welche die Informationen über Umläufe bündeln.
Die Umläufe benötigen einen `VehicleType`, welcher definiert, durch welchen Fahrzeugtyp der Umlauf bedient wird.
Fahrten können erstellt werden, indem zuerst `Route` Objekte erstellt werden.
Diese verknüpfen über Fremdschlüssel zwei `Station` Objekte.
Ein `Trip` kann erstellt werden, indem diese `Route` als Fremdschlüssel referenziert wird.
Außerdem enthält der `Trip` die Abfahrts- und Ankunftszeit sowie eine Referenz zum Umlauf über den Fremschlüssel `rotation`.
Die Simulation hat folgende Anforderungen an die `Trip` Objekte eines Umlaufs.

- der erste und letzte Trip müssen in einer Station mit charge_type = EnumChargeType.DEPOT starten
- Die Differenz aus Ankunfts- und Abfahrtszeit muss positiv sein (positive Fahrzeit)
- Die `arrival_station` der `Route` eines `Trips` muss identisch sein mit der `departure_station` der `Route` des direkt nachfolgenden `Trips`
- Die Differenz der `departure_time` eines `Trip` zu der `arrival_time` des direkt vorherigen `Trips` muss positiv oder null betragen (keine negative Standzeit)


# Toochain
Die Ergebnisse der Simulation werden in 4 Schritten erzeugt.
Diese Schritte werden im folgenden beschrieben.

## Verbrauchsberechnung
Das Szenario wird in eine SimBA kompatibles Objekt transformiert.
Nun werden für die Trips des Szenarios der Verbrauch berechnet.
Der Verbrauch is abhängig vom Fahrzeugtyp und der Distanz der Route des Trips.
Sollten Fahrzeugtypen eine Referenz zu einem `Consumption` Objekts mittels `VehicleClass` haben, so wird eine komplexere Verbrauchsberechnung genutzt.
Diese Methode nutzt die Geschwindigkeit der Fahrten, die Temperatur, den Beladungszustand sowie die Steigung der Route, um den Verbrauch zu bestimmen.

Die Fahrten eines Umlaufs starten mit einem vollen SoC, welcher wegen dem Energieverbrauch sukzessive abnimmt.
Sollte eine Fahrt an einer elektrifizieren Haltestelle halten, so wird das Fahrzeug geladen und der SoC steigt.
Welche Haltestellen elektrifiziert sind, konnte über den Reiter "Stationen" während der Szenariodefinition im Wizard eingestellt werden.
In speziellen Situationen kann auch eine Fahrt mit negativer Steigung zu einer Ladung der Batterie führen. Dies muss in dem entsprechenden `Consumption` Objekt abgebildet sein.



## Stations-Optimierung

Nach der ersten Verbrauchsberechnung kann eine Aussage über die Fahrbarkeit von Umläufen bei dem gegebenen Szenariorandbedingungen getroffen werden. Bei fahrbaren Umläufen besteht kein Handlungsbedarf.
Sollte ein Umlauf nicht fahrbar sein, so setzt die Stations-Optimierung von SimBA ein. Haltestellen die elektrifiziert werden können und als besonders vorteilhaft zeigen, werden durch WeBus elektrifiziert.
Dies soll dazu führen das Umläufe fahrbar werden.
Es kann jedoch nicht immer garantiert werden, dass eine Elektrifizierung von Haltestellen zu einer Fahrbarkeit der Umläufe führt.
Wenn eine Fahrbarkeit von Umläufen nicht erreicht werden kann, werden keine Stationen elektrifziert die zu einer Zuladung von Fahrzeugen dieser Umläufe führen.
Grund hierfür ist die Meinung, dass ein knapp nicht fahrbarer Umlauf keine Vorteile gegenüber einem deutlich nicht fahrbaren Umlauf hat.
Sollten Haltestellen aus beliebigen Gründen nicht elektrifizierbar sein, können diese über den Reiter "Stationen" während der Szenariodefinition im Wizard eingestellt werden.

## Depotoptimierung

## Konsolidierung
