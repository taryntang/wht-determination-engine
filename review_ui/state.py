"""Remember which proposal a reviewer actually saw before a button rerun."""
def displayed_version(state, determination_id, current_version):
    return state.setdefault(f'displayed-version:{determination_id}', current_version)
