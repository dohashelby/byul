"""Qualification data stays bounded/UTF8/unique/finite without runtime IO."""
import pytest
from guard.collectors.qualification_document import read


@pytest.mark.parametrize('value',[b'',b' '*32769,'{}',b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1e999}',
    '{}'.encode('utf-16'),b'\xff',b'['*2000+b'0'+b']'*2000],
    ids=['empty','large','not-bytes','duplicate','nan','overflow','utf16','invalid-utf8','deep'])
def test_ambiguous_or_out_of_bound_qualification_documents_are_refused(value):
    with pytest.raises(ValueError):read(value)


def test_finite_utf8_metadata_and_nested_records_keep_their_exact_values():
    assert read(b'{"ok":true,"rows":[{"elapsed_seconds":0.5}],"count":2}')=={
        'ok':True,'rows':[{'elapsed_seconds':0.5}],'count':2}
