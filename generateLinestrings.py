from shapely.geometry import LineString
from binascii import hexlify

# Define coordinates for a 3D LineString in Berlin (longitude, latitude, elevation)
berlin_coordinates_3d = [
    (13.404954, 52.520008, 10),  # Berlin coordinate 1 (with elevation 10)
    (13.407175, 52.515037, 15),  # Berlin coordinate 2 (with elevation 15)
    (13.403767, 52.506761, 20)   # Berlin coordinate 3 (with elevation 20)
    # Add more coordinates as needed to define the route with elevation
]

# Create a LineString object using the Berlin 3D coordinates
linestring_3d = LineString(berlin_coordinates_3d)

# Convert the 3D LineString to WKB format
wkb_representation_3d = linestring_3d.wkb_hex

# Display the WKB representation
print(wkb_representation_3d)