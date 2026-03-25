from enum import StrEnum


class LocationType(StrEnum):
    city = "city"
    airport = "airport"
    railway_station = "railway_station"
    bus_station = "bus_station"


class TransportType(StrEnum):
    plane = "plane"
    train = "train"
    bus = "bus"
