from rest_framework import serializers
from rest_framework.reverse import reverse


class NumericField(serializers.IntegerField):
    def to_representation(self, value):
        return int(float(value))


class IDHyperlinker(serializers.HyperlinkedRelatedField):
    def get_url(self, obj, view_name, request, format):
        url_kwargs = {"pk": str(obj).replace("/", ":")}
        return reverse(view_name, kwargs=url_kwargs, request=request, format=format)
