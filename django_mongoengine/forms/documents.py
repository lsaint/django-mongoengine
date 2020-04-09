from django.forms.forms import DeclarativeFieldsMetaclass
from django.forms.models import ALL_FIELDS
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.forms import models as model_forms
from django.utils import six

from mongoengine.fields import ObjectIdField, FileField
from mongoengine.errors import ValidationError


def construct_instance(form, instance, fields=None, exclude=None):
    """
    Constructs and returns a model instance from the bound ``form``'s
    ``cleaned_data``, but does not save the returned instance to the
    database.
    """
    opts = instance._meta

    cleaned_data = form.cleaned_data
    file_field_list = []
    for f in opts.fields:
        try:
            if (
                not f.editable
                or isinstance(f, ObjectIdField)
                or f.name not in cleaned_data
            ):
                continue
        except AttributeError:
            # probably this is StringField() added automatically for inherited fields
            # so we ignore it
            continue
        if fields is not None and f.name not in fields:
            continue
        if exclude and f.name in exclude:
            continue
        # Defer saving file-type fields until after the other fields, so a
        # callable upload_to can use the values from other fields.
        if isinstance(f, FileField):
            file_field_list.append(f)
        else:
            f.save_form_data(instance, cleaned_data[f.name])

    for f in file_field_list:
        f.save_form_data(instance, cleaned_data[f.name])

    return instance


class DocumentFormOptions(model_forms.ModelFormOptions):
    def __init__(self, options=None):
        super(DocumentFormOptions, self).__init__(options)
        self.model = getattr(options, "document", None) or getattr(
            options, "model", None
        )
        if self.model is not None:
            options.model = self.model
        self.embedded_field = getattr(options, "embedded_field", None)


class DocumentFormMetaclass(DeclarativeFieldsMetaclass):
    def __new__(mcs, name, bases, attrs):
        formfield_callback = attrs.pop("formfield_callback", None)

        new_class = super(DocumentFormMetaclass, mcs).__new__(mcs, name, bases, attrs)

        if bases == (BaseDocumentForm,):
            return new_class

        opts = new_class._meta = DocumentFormOptions(getattr(new_class, "Meta", None))

        # We check if a string was passed to `fields` or `exclude`,
        # which is likely to be a mistake where the user typed ('foo') instead
        # of ('foo',)
        for opt in ["fields", "exclude", "localized_fields"]:
            value = getattr(opts, opt)
            if isinstance(value, six.string_types) and value != ALL_FIELDS:
                msg = (
                    "%(model)s.Meta.%(opt)s cannot be a string. "
                    "Did you mean to type: ('%(value)s',)?"
                    % {"model": new_class.__name__, "opt": opt, "value": value}
                )
                raise TypeError(msg)

        if opts.model:
            # If a model is defined, extract form fields from it.
            if opts.fields is None and opts.exclude is None:
                raise ImproperlyConfigured(
                    "Creating a ModelForm without either the 'fields' attribute "
                    "or the 'exclude' attribute is prohibited; form %s "
                    "needs updating." % name
                )

            if opts.fields == ALL_FIELDS:
                # Sentinel for fields_for_model to indicate "get the list of
                # fields from the model"
                opts.fields = None

            if hasattr(opts, "field_classes"):
                fields = model_forms.fields_for_model(
                    opts.model,
                    opts.fields,
                    opts.exclude,
                    opts.widgets,
                    formfield_callback,
                    opts.localized_fields,
                    opts.labels,
                    opts.help_texts,
                    opts.error_messages,
                    opts.field_classes,
                )
            else:
                fields = model_forms.fields_for_model(
                    opts.model,
                    opts.fields,
                    opts.exclude,
                    opts.widgets,
                    formfield_callback,
                    opts.localized_fields,
                    opts.labels,
                    opts.help_texts,
                    opts.error_messages,
                )

            # make sure opts.fields doesn't specify an invalid field
            none_model_fields = [k for k, v in six.iteritems(fields) if not v]
            missing_fields = set(none_model_fields) - set(
                new_class.declared_fields.keys()
            )
            if missing_fields:
                message = "Unknown field(s) (%s) specified for %s"
                message = message % (", ".join(missing_fields), opts.model.__name__)
                raise FieldError(message)
            # Override default model fields with any custom declared ones
            # (plus, include all the other declared fields).
            fields.update(new_class.declared_fields)
        else:
            fields = new_class.declared_fields

        new_class.base_fields = fields

        return new_class


class BaseDocumentForm(model_forms.BaseModelForm):
    def _save_m2m(self):
        pass

    def _post_clean(self):
        opts = self._meta

        # mongo MetaDict does not have fields attribute
        # adding it here istead of rewriting code
        self.instance._meta.fields = opts.model._meta.fields
        exclude = self._get_validation_exclusions()

        try:
            self.instance = construct_instance(
                self, self.instance, opts.fields, exclude
            )
        except ValidationError as e:
            self._update_errors(e)

    def save(self, commit=True):
        """
        Saves this ``form``'s cleaned_data into model instance
        ``self.instance``.

        If commit=True, then the changes to ``instance`` will be saved to the
        database. Returns ``instance``.
        """

        if self.errors:
            try:
                if self.instance.pk is None:
                    fail_message = "created"
                else:
                    fail_message = "changed"
            except (KeyError, AttributeError):
                fail_message = "embedded document saved"
            raise ValueError(
                "The %s could not be %s because the data didn't"
                " validate." % (self.instance.__class__.__name__, fail_message)
            )

        if commit:
            self.instance.save()
        else:
            self.save_m2m = self._save_m2m

        return self.instance

    save.alters_data = True


@six.add_metaclass(DocumentFormMetaclass)
class DocumentForm(BaseDocumentForm):
    pass


def documentform_factory(
    model,
    form=DocumentForm,
    fields=None,
    exclude=None,
    formfield_callback=None,
    widgets=None,
    localized_fields=None,
    labels=None,
    help_texts=None,
    error_messages=None,
    *args,
    **kwargs
):
    return model_forms.modelform_factory(
        model,
        form,
        fields,
        exclude,
        formfield_callback,
        widgets,
        localized_fields,
        labels,
        help_texts,
        error_messages,
        *args,
        **kwargs
    )
