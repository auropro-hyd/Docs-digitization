from typing import Optional
from pydantic import BaseModel, Field



#page header consists of product name, document type, bpcr number & batch no & batch size. normalized for llm usage in structured output
class PageHeader(BaseModel):
    #page_number: str = Field(description="page number mentioned in the document")
    page_type: str = Field(description="type of the page - Batch Production Control Record, Raw Material request & issue etc")
    product_name: Optional[str] = Field(default=None, description="name of the product mentioned in the document, applicable only for BPCR")
    bpcr_number: Optional[str] = Field(default=None, description="Batch Production Control Record number mentioned in the document, applicable only for BPCR")
    batch_number: Optional[str] = Field(default=None, description="Batch number mentioned in the document, applicable only for BPCR")

class GenericTable(BaseModel):
    table_name: str
    headers: list[str] = Field(description="list of column headers in the table")
    values: list[list[str]] = Field(description="list of row values in the table")



class Signatures(BaseModel):
    is_operator_attested: bool | None = Field(description="set to false if '-' is observed in the Done by column or related column, if empty set to None,  true otherwise")
    is_reviewer_attested: bool | None = Field(description="set to false if '-' is observed in the Checked by column or related column, if empty set to None, true otherwise")

class RawMaterialUsedSteps(BaseModel):
    used_step_no: str = Field(description="Step numbers where the raw material is used")
    batch_no: str = Field(description="Batch number mentioned for the raw material in the step, same batch number can be used for multiple raw materials in the same step")
    standard_quantity: str = Field(description="Standard quantity mentioned for the raw material in the step")
    gross_quantity: Optional[str] = Field(default=None, description="Gross quantity mentioned for the raw material in the step, if '-' is observed in the cell, set it to None")
    net_quantity: str = Field(description="Net quantity mentioned for the raw material in the step, if '-' is observed in the cell, set it to None")
    tare_quantity: Optional[str] = Field(default=None, description="Tare quantity mentioned for the raw material in the step, if '-' is observed in the cell, set it to None")
    signatures: Signatures = Field(description="Signatures information for the raw material in the step")

class RawMaterialAndWeighingRecord(BaseModel):
    s_no: int = Field(description="Serial number of the raw material")
    raw_material_name_item_code: str = Field(description="value from 'Raw Material Name / Item Code' column")
    uom: str = Field(description="value from 'UOM' column")
    used_in_steps: list[RawMaterialUsedSteps] = Field(description="List of steps where the raw material is used")

class RawMaterialTable(BaseModel):
    raw_materials: list[RawMaterialAndWeighingRecord] = Field(description="List of raw materials mentioned in the Raw Material table")

class ManufacturingInstruction(BaseModel):
    step_no: str = Field(description="Step number mentioned in the Step no. column")
    sub_step_no: Optional[str] = Field(default=None, description="Sub step number mentioned in the operation column. if not present, set it to None")
    is_critical_step: bool = Field(description="Set this field to true if the operation includes any numeric quantity (for example, values with units such as 800 L, 4 KG, 7.0) or contains inspection-related keywords (such as inspect) or uses explicit emphasis formatting (for example, text wrapped in <b>...</b> tags")
    operation: str = Field(description="Operation mentioned in operation column")
    equipment_used: Optional[str] = Field(default=None, description="Equipment used mentioned in operation column")
    start_time: Optional[str] = Field(default=None, description="start time mentioned in Time(Hr-min)/From column. always in hh-mm format, if '-' is observed, set it to None")
    end_time: Optional[str] = Field(default=None, description="end time mentioned in Time(Hr-min)/To column,if '-' is observed, set it to None")
    duration: Optional[str] = Field(default=None, description="duration mentioned in Time(Hr-min)/Duration column,if '-' is observed, set it to None")
    remarks: Optional[str] = Field(default=None, description="remarks mentioned in the Remarks column,if '-' is observed, set it to None")
    signatures: Signatures = Field(description="Signatures information in done by / checked by columns for that step")
    notes: Optional[str] = Field(default=None, description="notes mentioned for that manufacturing instruction step, if any, else None")

class TemperatureRecord(BaseModel):
    time: str = Field(description="Time mentioned in the Temperature Record table")
    temperature: str = Field(description="Temperature mentioned in the Temperature Record table")
    initials: str = Field(description="Initials mentioned in the Temperature Record table")

class TemperatureRecordTable(BaseModel):
    previous_step: str = Field(description="Previous Step mentioned in the Temperature Record table")
    records: Optional[list[TemperatureRecord]] = Field(default=None, description="List of temperature records mentioned in the Temperature Record table")
    is_table_na: bool = Field(description="Whether the Temperature Record table is marked as N/A in the document or the table do not have any entries for time, temperature & initials columns")
    distillate_vol: Optional[str] = Field(default=None, description="Distillate Volume mentioned in the Temperature Record table")
    b_no: Optional[str] = Field(default=None, description="Batch Number mentioned in the Temperature Record table")

class RawMaterialAndWeighingTable(BaseModel):
    raw_materials: list[RawMaterialAndWeighingRecord] = Field(description="List of raw materials and their weighing records")


class WeighingRecord(BaseModel):
    container_no: str = Field(description="Container number mentioned in the Weighing Record table")
    tare_weight: str = Field(description="Tare weight mentioned in the Weighing Record table")
    gross_weight: str = Field(description="Gross weight mentioned in the Weighing Record table")
    net_weight: str = Field(description="Net weight mentioned in the Weighing Record table")

class WeighingRecordTable(BaseModel):
    records: list[WeighingRecord] = Field(description="List of weighing records mentioned in the Weighing Record table")
    total_weight: Optional[str] = Field(default=None, description="Total weight mentioned in the Weighing Record table")
    weighing_balance_id: Optional[str] = Field(default=None, description="Weighing Balance ID mentioned in the Weighing Record table")
    signatures: Signatures = Field(description="Signatures information for the Weighing Record table")

class AllWeighingRecordTables(BaseModel):
    previous_step: str = Field(description="Previous Step mentioned in the Weighing Record tables")
    weighing_tables: list[WeighingRecordTable] = Field(description="List of all Weighing Record Tables in the document")
    total_wet_weight: Optional[str] = Field(default=None, description="Total wet weight mentioned across all Weighing Record tables in the document")

class vaccume_record(BaseModel):
    time: str = Field(description="Time mentioned in the Vaccume Record table")
    temperature: str = Field(description="Temperature mentioned in the Vaccume Record table")
    vaccume: str = Field(description="Vaccume mentioned in the Vaccume Record table")
    initials: str = Field(description="Initials mentioned in the Vaccume Record table")

class VaccumeRecordTable(BaseModel):
    previous_step: str = Field(description="Previous Step mentioned in the Vaccume Record table")
    is_table_na: bool = Field(description="Whether the Vaccume Record table is marked as N/A in the document or the table do not have any entries for time, temperature, vaccume & initials columns")
    records: Optional[list[vaccume_record]] = Field(default=None, description="List of vaccume records mentioned in the Vaccume Record table")
    distillate_vol: Optional[str] = Field(default=None, description="Distillate Volume mentioned in the Vaccume Record table")
    b_no: Optional[str] = Field(default=None, description="Batch Number mentioned in the Vaccume Record table")

class ManufacturingInstructionsTable(BaseModel):
    instructions: list[ManufacturingInstruction] = Field(description="List of manufacturing instructions")
    temperature_record_table: Optional[TemperatureRecordTable] = Field(default=None, description="Temperature Record Table if present in the manufacturing instructions page, else None")
    weight_record_tables: Optional[AllWeighingRecordTables] = Field(default=None, description="All Weighing Record Tables if present in the manufacturing instructions page, else None")
    vaccume_record_table: Optional[VaccumeRecordTable] = Field(default=None, description="Vaccume Record Table if present in the manufacturing instructions page, else None")
    weight_record_after_vaccume_tables: Optional[AllWeighingRecordTables] = Field(default=None, description="All Weighing Record Tables after vaccum operation if present in the manufacturing instructions page, else None")

class YieldDetails(BaseModel):
    theoretical_weight: str = Field(description="Theoretical Weight mentioned in the Yield Details section")
    obtained_weight: str = Field(description="Obtained Weight mentioned in the Yield Details section")
    yield_range: str = Field(description="Yield Range mentioned in the Yield Details section")
    yield_percentage: str = Field(description="Yield Percentage mentioned in the Yield Details section")
    signatures: Signatures = Field(description="Signatures information for the Yield Details section")

class RequestedAnalysisReport(BaseModel):
    name: str = Field(description="Name of the requested analysis report")
    time: Optional[str] = Field(default=None, description="Time mentioned for the requested analysis report, if any, else None")

class ShiftingRecord(BaseModel):
    inspect: str = Field(description="Inspect mentioned in the Shifting Record table")
    date: str = Field(description="Date mentioned in the Shifting Record table")
    time: str = Field(description="Time mentioned in the Shifting Record table in hh-mm format")
    signatures: Signatures = Field(description="Signatures information for the Shifting Record table")
    input_quantity: Optional[str] = Field(default=None, description="Input Quantity mentioned in the Shifting Record table, if any, else None")
    mesh_size: Optional[str] = Field(default=None, description="Mesh Size mentioned in the Shifting Record table, if any, else None")
    operation_start: Optional[str] = Field(default=None, description="Operation Start time mentioned in the Shifting Record table in hh-mm format, if any, else None")
    operation_end: Optional[str] = Field(default=None, description="Operation End time mentioned in the Shifting Record table in hh-mm format, if any, else None")
    operation_duration: Optional[str] = Field(default=None, description="Operation Duration mentioned in the Shifting Record table, if any, else None")
    signatures: Optional[Signatures] = Field(default=None, description="Signatures information for the Shifting Record table, if any, else None")
    is_qa_attested: Optional[bool] = Field(default=None, description="Whether QA signed along with date in the QA sign column, if any, else None")

class PinMillingRecord(BaseModel):
    inspect: str = Field(description="Inspect mentioned in the Shifting Record table")
    date: str = Field(description="Date mentioned in the Shifting Record table")
    time: str = Field(description="Time mentioned in the Shifting Record table in hh-mm format")
    signatures: Signatures = Field(description="Signatures information for the Shifting Record table")
    input_quantity: Optional[str] = Field(default=None, description="Input Quantity mentioned in the Shifting Record table, if any, else None")
    rpm: Optional[str] = Field(default=None, description="RPM mentioned in the Shifting Record table, if any, else None")
    operation_start: Optional[str] = Field(default=None, description="Operation Start time mentioned in the Shifting Record table in hh-mm format, if any, else None")
    operation_end: Optional[str] = Field(default=None, description="Operation End time mentioned in the Shifting Record table in hh-mm format, if any, else None")
    operation_duration: Optional[str] = Field(default=None, description="Operation Duration mentioned in the Shifting Record table, if any, else None")
    signatures: Optional[Signatures] = Field(default=None, description="Signatures information for the Shifting Record table, if any, else None")
    
class MixingOperation(BaseModel):
    operation: str = Field(description="Mixing operation mentioned in the Mixing Operations table")
    standard_time: str = Field(description="Standard time mentioned in the Mixing Operations table hh-mm format")
    start_time: Optional[str] = Field(default=None, description="Start time mentioned in the Mixing Operations table hh-mm format, if '-' is observed, set it to None")
    end_time: Optional[str] = Field(default=None, description="End time mentioned in the Mixing Operations table hh-mm format, if '-' is observed, set it to None")
    duration: Optional[str] = Field(default=None, description="Duration mentioned in the Mixing Operations table, if '-' is observed, set it to None")
    signatures: Signatures = Field(description="Signatures information for the Mixing Operations table")

class MixingRecord(BaseModel):
    inspect: str = Field(description="Inspect mentioned in the Mixing Record table")
    date: str = Field(description="Date mentioned in the Mixing Record table")
    time: str = Field(description="Time mentioned in the Mixing Record table in hh-mm format")
    shift: str = Field(description="Shift mentioned in the Mixing Record table")
    signatures: Signatures = Field(description="Signatures information for the Mixing Record table")
    mixing_operations: list[MixingOperation] = Field(description="List of mixing operations mentioned in the Mixing Operations table")

class MicronizationOperation(BaseModel):
    trail_no: str = Field(description="Trial number mentioned in the Micronization Operations table")
    operations: ManufacturingInstruction = Field(description="Manufacturing instruction details for the Micronization Operations table")

class MicronizationOperationRecord(BaseModel):
    inspect: str = Field(description="Inspect mentioned in the Micronization Operation Record table")
    date: str = Field(description="Date mentioned in the Micronization Operation Record table")
    time: str = Field(description="Time mentioned in the Micronization Operation Record table in hh-mm format")
    shift: str = Field(description="Shift mentioned in the Micronization Operation Record table")
    signtures: Signatures = Field(description="Signatures information for the Micronization Operation Record table")
    input_size: Optional[str] = Field(default=None, description="Input Size mentioned in the Micronization Operation Record table, if any, else None")
    pressure_header: Optional[str] = Field(default=None, description="Pressure Header mentioned in the Micronization Operation Record table, if any, else None")
    pressure_base: Optional[str] = Field(default=None, description="Pressure Base mentioned in the Micronization Operation Record table, if any, else None")
    feeder_pressure: Optional[str] = Field(default=None, description="Feeder Pressure mentioned in the Micronization Operation Record table, if any, else None")
    feeder_frequency: Optional[str] = Field(default=None, description="Feeder Frequency or RPM mentioned in the Micronization Operation Record table, if any, else None")
    remarks: Optional[str] = Field(default=None, description="Remarks mentioned in the Micronization Operation Record table, if any, else None")

class ComillOperation(BaseModel):
    operations: ManufacturingInstruction = Field(description="Manufacturing instruction details for the Comill Operations table")

class ComillOperationRecord(BaseModel):
    inspect: str = Field(description="Inspect mentioned in the Comill Operation Record table")
    date: str = Field(description="Date mentioned in the Comill Operation Record table")
    time: str = Field(description="Time mentioned in the Comill Operation Record table in hh-mm format")
    signatures: Signatures = Field(description="Signatures information for the Comill Operation Record table")
    input_quantity: Optional[str] = Field(default=None, description="Input Quantity mentioned in the Comill Operation Record table, if any, else None")
    rpm: Optional[str] = Field(default=None, description="RPM mentioned in the Comill Operation Record table, if any, else None")
    start_time: Optional[str] = Field(default=None, description="Start time mentioned in the Comill Operation Record table in hh-mm format, if '-' is observed, set it to None")
    end_time: Optional[str] = Field(default=None, description="End time mentioned in the Comill Operation Record table in hh-mm format, if '-' is observed, set it to None")
    duration: Optional[str] = Field(default=None, description="Duration mentioned in the Comill Operation Record table, if '-' is observed, set it to None")
    operation_signatures: Optional[Signatures] = Field(default=None, description="Signatures information for the Comill Operation Record table, if any, else None")

class MetalDetectionOperation(BaseModel):
    operations: ManufacturingInstruction = Field(description="Manufacturing instruction details for the Metal Detection Operations table")
    inspect: str = Field(description="Inspect mentioned in the metal detector Record table")
    date: str = Field(description="Date mentioned in the metal detector Record table")
    time: str = Field(description="Time mentioned in the metal detector Record table in hh-mm format")
    signatures: Signatures = Field(description="Signatures information for the metal detector Record table")
    input_quantity: Optional[str] = Field(default=None, description="Input Quantity mentioned in the metal detector Record table, if any, else None")
    start_time: Optional[str] = Field(default=None, description="Start time mentioned in the metal detector Record table in hh-mm format, if '-' is observed, set it to None")
    end_time: Optional[str] = Field(default=None, description="End time mentioned in the metal detector Record table in hh-mm format, if '-' is observed, set it to None")
    duration: Optional[str] = Field(default=None, description="Duration mentioned in the metal detector Record table, if '-' is observed, set it to None")
    operation_signatures: Optional[Signatures] = Field(default=None, description="Signatures information for the metal detector Record table, if any, else None")

class ReconciliationRecord(BaseModel):
    quantity: str = Field(description="Quantity mentioned in the Reconciliation Record table")
    remarks: Optional[str] = Field(default=None, description="Remarks mentioned in the Reconciliation Record table, if any, else None")
class ReconciliationDetails(BaseModel):
    after_drying: ReconciliationRecord = Field(description="Reconciliation details after drying")
    power_processing: ReconciliationRecord = Field(description="Reconciliation details for power processing")
    sample_quantity: ReconciliationRecord = Field(description="Reconciliation details for sample quantity")
    final_output: ReconciliationRecord = Field(description="Reconciliation details for final output")

class EquipmentCleaningRecord(BaseModel):
    name: str = Field(description="Name of the equipment being cleaned")
    id: str = Field(description="ID of the equipment being cleaned")
    prev_batch_no: str = Field(description="Previous batch number processed using the equipment")
    start_time: str = Field(description="Start time of the cleaning process in hh-mm format")
    end_time: str = Field(description="End time of the cleaning process in hh-mm format")
    duration: str = Field(description="Duration of the cleaning process in hh-mm format")
    signatures: Signatures = Field(description="Signatures information for the Equipment Cleaning Record")

class EquipmentCleaningDetails(BaseModel):
    records: list[EquipmentCleaningRecord] = Field(description="List of equipment cleaning records in the document")

class DeviationRecord(BaseModel):
    daate: str = Field(description="Date mentioned in the Deviation Record table")
    details: str = Field(description="Details mentioned in the Deviation Record table")
    justification: str = Field(description="Justification mentioned in the Deviation Record table")
    is_signed: bool = Field(description="Whether signed with initials or date in the Deviation Record table else false")

class DeviationDetails(BaseModel):
    records: list[DeviationRecord] = Field(description="List of deviation records in the document")
    completion_date: Optional[str] = Field(default=None, description="Completion date mentioned in the Deviation Details section, if any, else None")
    completion_time: Optional[str] = Field(default=None, description="Completion time mentioned in the Deviation Details section in hh-mm format, if any, else None")
    is_approveby_mc: Optional[bool] = Field(default=None, description="Whether approved by manufacturing chemist with signature or date in the Deviation Details section, if any, else None")
    is_approveby_qcc: Optional[bool] = Field(default=None, description="Whether approved by quaity control chemist with signature or date in the Deviation Details section, if any, else None")
    is_appprovedby_qa: Optional[bool] = Field(default=None, description="Whether approved by quality assurance officer with signature or date in the Deviation Details section, if any, else None")

class BpcrPage(BaseModel):
    page_header: PageHeader = Field(description="Header information of the BPCR page")
    raw_material_and_weighing_table: Optional[RawMaterialTable] = Field(default=None, description="Raw Material and Weighing Table if present in the page, else None")
    generic_tables: Optional[list[GenericTable]] = Field(default=None, description="List of any other generic tables present in the page, else None")
    manufacturing_instructions_table: Optional[ManufacturingInstructionsTable] = Field(default=None, description="Manufacturing Instructions if present in the page, else None")
    yield_details: Optional[YieldDetails] = Field(default=None, description="Yield Details if present in the page, else None")
    requested_analysis_reports: Optional[list[RequestedAnalysisReport]] = Field(default=None, description="List of Requested Analysis Reports if present in the page, else None")
    shifting_records: Optional[list[ShiftingRecord]] = Field(default=None, description="Shifting Records if present in the page, else None")
    pin_milling_records: Optional[list[PinMillingRecord]] = Field(default=None, description="Pin Milling Records if present in the page, else None")
    mixing_records: Optional[list[MixingRecord]] = Field(default=None, description="Mixing Records if present in the page, else None")
    micronization_operations: Optional[list[MicronizationOperation]] = Field(default=None, description="List of Micronization Operations if present in the page, else None")
    micronization_operation_record: Optional[MicronizationOperationRecord] = Field(default=None, description="Micronization Operation Record if present in the page, else None")
    shifting_after_micronization_records: Optional[list[ShiftingRecord]] = Field(default=None, description="Shifting Records after Micronization if present in the page, else None")
    comill_operation: Optional[ComillOperation] = Field(default=None, description="Comill Operation if present in the page, else None")
    comill_operation_record: Optional[list[ComillOperationRecord]] = Field(default=None, description="Comill Operation Record if present in the page, else None")
    metal_detection_operation: Optional[MetalDetectionOperation] = Field(default=None, description="Metal Detection Operation if present in the page, else None")
    final_weighing_record: Optional[WeighingRecordTable] = Field(default=None, description="Final Weighing Record if present in the page after metal detection record, else None")
    reconciliation_details: Optional[ReconciliationDetails] = Field(default=None, description="Reconciliation Details if present in the page, else None")
    equipment_cleaning_details: Optional[EquipmentCleaningDetails] = Field(default=None, description="Equipment Cleaning Details if present in the page, else None")
    deviation_details: Optional[DeviationDetails] = Field(default=None, description="Deviation Details if present in the page, else None")
    notes: Optional[str] = Field(default=None, description="Any additional notes mentioned at the page level (not specific to a step), if any, else None")
    printed_by: Optional[str] = Field(default=None, description="Printed By information mentioned in the page, if any, else None")
    printed_date: Optional[str] = Field(default=None, description="Printed Date information mentioned in the page, if any, else None")
    page_summary: Optional[str] = Field(default=None, description="The summary should state what activities are recorded (e.g., material dispensing, processing steps, checks, observations, calculations, or sign-offs) and their order, based only on visible page content. DO NOT include page header information")
    section_name: Optional[str] = Field(default=None, description="Name of the section to which this page belongs, if any, else None")
    sub_section_name: Optional[str] = Field(default=None, description="Name of the sub-section to which this page belongs, if any, else None")
    

class BpcrPages(BaseModel):
    pages: list[BpcrPage] = Field(description="List of BPCR pages in the document")