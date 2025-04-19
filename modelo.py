gspread.exceptions.APIError: This app has encountered an error. The original error message is redacted to prevent data leaks. Full error details have been recorded in the logs (if you're on Streamlit Cloud, click on 'Manage app' in the lower right of your app).
Traceback:
File "/mount/src/streamly/modelo.py", line 26, in <module>
    LOGIN_SHEET = client_gspread.open("LoginSimulador").sheet1
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.12/site-packages/gspread/client.py", line 157, in open
    return Spreadsheet(self.http_client, properties)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.12/site-packages/gspread/spreadsheet.py", line 29, in __init__
    metadata = self.fetch_sheet_metadata()
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.12/site-packages/gspread/spreadsheet.py", line 230, in fetch_sheet_metadata
    return self.client.fetch_sheet_metadata(self.id, params=params)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.12/site-packages/gspread/http_client.py", line 305, in fetch_sheet_metadata
    r = self.request("get", url, params=params)
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.12/site-packages/gspread/http_client.py", line 128, in request
    raise APIError(response)
