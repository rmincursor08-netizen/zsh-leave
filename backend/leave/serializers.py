from rest_framework import serializers


class LeaveTypeMutationSerializer(serializers.Serializer):
    key = serializers.RegexField(
        r"^[a-z][a-z0-9_]{1,31}$",
        min_length=2,
        max_length=32,
        required=False,
        error_messages={"invalid": "کلید باید با حرف کوچک آغاز شود و فقط حروف کوچک، عدد یا _ داشته باشد"},
    )
    labelFa = serializers.CharField(max_length=128, required=False)
    color = serializers.CharField(max_length=7, required=False)
    countsAgainstBalance = serializers.BooleanField(required=False)
    requiresApproval = serializers.BooleanField(required=False)
    requiresNote = serializers.BooleanField(required=False)
    requiresReplacement = serializers.BooleanField(required=False)
    employeeRequestable = serializers.BooleanField(required=False)
    confidential = serializers.BooleanField(required=False)
    enabled = serializers.BooleanField(required=False)
    sortOrder = serializers.IntegerField(required=False)
    reason = serializers.CharField(
        trim_whitespace=True,
        min_length=1,
        max_length=500,
        error_messages={
            "blank": "دلیل تغییر الزامی است",
            "min_length": "دلیل تغییر الزامی است",
            "max_length": "دلیل تغییر حداکثر ۵۰۰ نویسه است",
        },
    )
