from shapely.geometry import LineString
import sys
from binascii import hexlify

def generate_berlin_linestring():
    # Define coordinates representing a LineString through Berlin (example coordinates)
    berlin_coordinates = [
        (13.39331130217266,52.5247925575945,  10),  # Berlin coordinate 1 (with elevation 10)
        (13.418604261815728,52.5007655645829,  15),  # Berlin coordinate 2 (with elevation 15)
        (13.446489404164762,52.555729699234966,  20),  # Berlin coordinate 3
        (13.39331130217266,52.5247925575945, 10)
    ]

    # Create a LineString object using the Berlin coordinates
    linestring = LineString(berlin_coordinates)

    # Convert the LineString to WKB format
    wkb_representation = linestring.wkb_hex

    return wkb_representation

if __name__ == "__main__":
    wkb_result = generate_berlin_linestring()

    # Print the WKB representation to the terminal
    print(f"WKB Representation: {wkb_result}")