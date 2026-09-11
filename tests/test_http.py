import pytest
from lancet_funding.http import HttpDsl
from lancet_funding.io import InputError

class Response:
    def __init__(self,status,obj,headers=None):self.status_code=status;self.obj=obj;self.headers=headers or {}
    def json(self):return self.obj
class Session:
    def __init__(self,responses):self.responses=responses;self.calls=[]
    def post(self,url,**kwargs):self.calls.append((url,kwargs));return self.responses.pop(0)

def test_authentication_protocol_and_no_token_in_result():
    session=Session([Response(200,{'token':'synthetic-token'}),Response(200,{'grants':[]})])
    api=HttpDsl('synthetic-key','https://app.dimensions.ai',session=session)
    result=api.query('test-query')
    assert session.calls[0][0].endswith('/api/auth')
    assert session.calls[0][1]['json']=={'key':'synthetic-key'}
    assert session.calls[1][0].endswith('/api/dsl/v2')
    assert session.calls[1][1]['headers']['Authorization']=='JWT synthetic-token'
    assert 'token' not in result.json
    assert session.calls[1][1]['allow_redirects'] is False

def test_expired_token_is_refreshed_once():
    session=Session([Response(200,{'token':'one'}),Response(401,{}),Response(200,{'token':'two'}),Response(200,{'grants':[]})])
    assert HttpDsl('synthetic-key','https://app.dimensions.ai',session=session).query('q').json=={'grants':[]}
    assert len(session.calls)==4

@pytest.mark.parametrize('status',[302,400,401,403,500])
def test_auth_error_does_not_echo_secret(status):
    session=Session([Response(status,{'error':'synthetic-sensitive-key'})])
    with pytest.raises(InputError) as error:
        HttpDsl('synthetic-sensitive-key','https://app.dimensions.ai',session=session).query('q')
    assert 'synthetic-sensitive-key' not in str(error.value)
