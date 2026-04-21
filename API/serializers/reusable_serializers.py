from rest_framework import serializers

from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class YearSerializer(PVAPIDocumentSerializer):
    year = serializers.IntegerField(required=False)
    num_patents = serializers.IntegerField(required=False)
