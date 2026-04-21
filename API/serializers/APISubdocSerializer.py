from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied


class PVAPIDocumentSerializer(serializers.Serializer):
    def update(self, instance, validated_data):
        raise PermissionDenied("PatentsView does not receive patent data from users")

    def create(self, validated_data):
        raise PermissionDenied("PatentsView does not receive patent data from users")
