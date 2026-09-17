from typing import Any

import pandas as pd


def get_delete_list_from_source(
    df: pd.DataFrame,
    field_to_filter_on: str,
    field_to_return: str,
    runid: str,
) -> list[Any]:
    return df[
        (df["CurrentDeleteFlag"] == True)
        & (df[field_to_filter_on].apply(lambda x: x == True if isinstance(x, bool) else False))
        & (df["RunId"] == runid)
    ][field_to_return].tolist()


def apply_deletion_results(
    df: pd.DataFrame,
    delete_object_results: dict[str, Any],
    identifier_col: str,
    exists_after_col: str,
    delete_timestamp_col: str,
    after_identifiers: list[Any],
    run_id: str,
) -> pd.DataFrame:
    required_columns = {"RunId", identifier_col}
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        raise KeyError(f"Missing required columns: {sorted(missing_columns)}")

    if exists_after_col not in df.columns:
        df[exists_after_col] = pd.Series(None, index=df.index, dtype="object")
    else:
        df[exists_after_col] = df[exists_after_col].astype("object").where(df[exists_after_col].notna(), None)

    if delete_timestamp_col not in df.columns:
        df[delete_timestamp_col] = pd.Series(None, index=df.index, dtype="object")
    else:
        df[delete_timestamp_col] = (
            df[delete_timestamp_col].astype("object").where(df[delete_timestamp_col].notna(), None)
        )

    deleted_ids = set(delete_object_results.get("deleted_actual", []))
    after_identifier_set = {
        identifier for identifier in after_identifiers if identifier is not None and not pd.isna(identifier)
    }
    deletion_timestamp = delete_object_results.get("delete_timestamp")

    current_run_mask = df["RunId"].eq(run_id)
    applicable_mask = current_run_mask & df[identifier_col].notna()
    not_applicable_mask = current_run_mask & df[identifier_col].isna()
    successfully_deleted_mask = applicable_mask & df[identifier_col].isin(deleted_ids)

    df.loc[not_applicable_mask, exists_after_col] = None
    df.loc[not_applicable_mask, delete_timestamp_col] = None

    df.loc[applicable_mask, exists_after_col] = (
        df.loc[applicable_mask, identifier_col].isin(after_identifier_set).astype(object)
    )
    df.loc[applicable_mask, delete_timestamp_col] = None
    df.loc[successfully_deleted_mask, delete_timestamp_col] = deletion_timestamp

    return df
