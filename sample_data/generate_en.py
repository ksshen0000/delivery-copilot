"""Generate the English version of the fictional project in project_a (same facts, same numbers).

It backs the English eval set, so Chinese and English results can be compared on one project.
All companies, people and numbers are invented. Run from the backend environment:
    cd backend && uv run python ../sample_data/generate_en.py
Output goes to sample_data/project_a_en/.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate import write_docx, write_xlsx  # noqa: E402

OUT = Path(__file__).resolve().parent / 'project_a_en'

CONTRACT = [
    ('h', 'Equipment Purchase and Installation Contract'),
    ('p', 'Contract No.: QM-2026-0412'),
    ('p', 'Buyer: Huaheng Auto Parts (Suzhou) Co., Ltd.'),
    ('p', 'Supplier: Qiming Intelligent Equipment Co., Ltd.'),
    ('h', 'Article 1 Subject Matter'),
    ('p', 'The Supplier shall provide the Buyer with one "Steering Knuckle Machining Automated Loading/Unloading Line" '
          '(the "Line"), including equipment design, manufacturing, transport, installation, commissioning, training and '
          'acceptance services. The configuration, technical parameters and scope of supply of the Line are defined by the '
          'Technical Agreement (No. QM-TA-2026-0412) signed by both parties, which forms an integral part of this contract.'),
    ('h', 'Article 2 Contract Price'),
    ('p', 'The total contract price is CNY 3.86 million (including 13% VAT), covering equipment, transport, installation '
          'and commissioning, and training. Work outside the scope of the Technical Agreement is priced separately under '
          'the change clause in Article 8.'),
    ('h', 'Article 3 Payment Terms'),
    ('table', [['Milestone', 'Share', 'Amount (CNY 10k)', 'Condition'],
               ['Advance payment', '30%', '115.8', 'Within 10 working days after the contract is signed'],
               ['Shipment payment', '30%', '115.8', 'After the factory acceptance test (FAT) is passed and the Buyer signs the FAT report'],
               ['Acceptance payment', '30%', '115.8', 'After the site acceptance test (SAT) is passed and the Buyer signs the final acceptance certificate'],
               ['Retention', '10%', '38.6', 'Within 15 working days after the warranty period ends with no open quality issues']]),
    ('h', 'Article 4 Delivery'),
    ('p', 'The Supplier shall complete the FAT within 120 calendar days after the contract is signed, and ship to Workshop 2 '
          "of the Buyer's Suzhou plant within 15 calendar days after the FAT is passed. Delays caused by the Buyer (including "
          'but not limited to late drawing approval, site not ready, or samples not provided on time) extend the delivery '
          'time accordingly.'),
    ('h', 'Article 5 Acceptance'),
    ('p', 'Acceptance has two stages, the factory acceptance test (FAT) and the site acceptance test (SAT); the criteria and '
          'methods are given in Chapter 6 of the Technical Agreement. The SAT shall take place within 45 calendar days after '
          'installation on site is complete. If the SAT is postponed by more than 60 calendar days for reasons attributable '
          'to the Buyer, final acceptance is deemed passed.'),
    ('h', 'Article 6 Warranty'),
    ('p', 'The warranty period is 12 months from the date the final acceptance is passed. During the warranty period, the '
          'Supplier repairs or replaces free of charge any failure caused by quality defects of the equipment itself. The '
          'Supplier shall respond within 4 hours of a failure notice and arrive on site within 48 hours. Wear parts (gripper '
          'fingertips, suction cups, vision light source) are not covered by free replacement.'),
    ('h', 'Article 7 Liability for Breach'),
    ('p', 'If delivery is late for reasons attributable to the Supplier, the Supplier pays liquidated damages of 0.1% of the '
          'total contract price per day of delay, capped at 5% of the total contract price. If the Buyer pays late, the '
          'Buyer pays 0.05% of the overdue amount per day of delay.'),
    ('h', 'Article 8 Changes'),
    ('p', 'Any change beyond the scope of the Technical Agreement proposed by either party shall be submitted as a written '
          'Engineering Change Order and takes effect once signed by both project managers. The change order shall state the '
          'change, its cost and its impact on the delivery date. The Supplier may refuse to carry out verbal changes that '
          'have not been confirmed in writing; for changes already carried out, the cost is negotiated separately.'),
    ('h', 'Article 9 Miscellaneous'),
    ('p', 'This contract is made in four copies, two for each party, and takes effect when signed and sealed by both '
          'parties. Date of signing: April 12, 2026.'),
]

TECH_AGREEMENT = [
    ('h', 'Steering Knuckle Machining Automated Loading/Unloading Line - Technical Agreement'),
    ('p', 'Agreement No.: QM-TA-2026-0412  Version: V1.2  Date: April 8, 2026'),
    ('h', 'Chapter 1 Project Overview'),
    ('p', "The Buyer's Workshop 2 has 4 vertical machining centers (VMC-01 to VMC-04, model HTC-850), currently loaded and "
          'unloaded by hand. This project adds a robotic loading/unloading system that automates the flow of steering '
          'knuckle blanks from the magazine to the machines, the transfer of workpieces between machines for successive '
          'operations, and the unloading of finished parts, saving 3 operators per shift.'),
    ('h', 'Chapter 2 Workpieces'),
    ('table', [['Item', 'Parameter'],
               ['Workpiece', 'Steering knuckle (cast iron QT450), 2 models in total: ZX-A01, ZX-A02'],
               ['Blank weight', '6.2 kg (ZX-A01), 7.8 kg (ZX-A02)'],
               ['Dimensions', 'Not larger than 320 × 260 × 210 mm'],
               ['Changeover', 'Changeover time between the two models no more than 20 minutes (including gripper change)']]),
    ('h', 'Chapter 3 Scope of Supply'),
    ('p', '3.1 Robots: 2 six-axis industrial robots (FANUC M-20iD/25), with controllers and teach pendants, plus 1 seventh-axis '
          'floor track (travel 8 m).'),
    ('p', '3.2 End effectors: 2 sets of dual-station pneumatic grippers (1 set per model), with quick-change plates.'),
    ('p', '3.3 Magazines: 1 blank magazine and 1 finished-part magazine, both 6-tier drawer type, 8 parts per tier, with '
          'in-position detection.'),
    ('p', '3.4 Vision: 1 2D vision system for locating blanks in the magazine, positioning accuracy ±0.5 mm.'),
    ('p', '3.5 Safety: safety fence, safety door locks, safety light curtains and emergency stop circuit, compliant with '
          'GB 11291.2.'),
    ('p', '3.6 Electrical control: 1 main PLC (Siemens S7-1500) and 1 HMI touch panel, including the line logic and the '
          'signal exchange with the 4 machine tools. The machine side must provide automatic door, chuck clamp/unclamp, '
          'machining complete and alarm signals; the Buyer coordinates the machine tool manufacturer to complete the '
          'machine-side modification.'),
    ('p', "3.7 Data interface: the PLC provides output, cycle time, equipment status and alarm data to the Buyer's MES via "
          "OPC UA; points are listed in the Point List. Data collection and dashboard development on the MES side are not "
          "in the Supplier's scope."),
    ('p', '3.8 Traceability: Workpiece traceability is not included in this phase (marking, code reading, barcode binding). '
          'The PLC reserves 1 PROFINET device slot for later expansion.'),
    ('h', 'Chapter 4 Performance'),
    ('table', [['Indicator', 'Requirement', 'Note'],
               ['Cycle time per part', '≤ 45 s/part', 'ZX-A01, at full load of 4 machine tools'],
               ['Overall equipment effectiveness OEE (automation part)', '≥ 90%', "Excluding machine tool failures and Buyer's material problems"],
               ['Pick success rate', '≥ 99.5%', 'Measured over 1000 consecutive picks'],
               ['Magazine autonomous run time', '≥ 60 minutes', 'Fully loaded, without operator intervention']]),
    ('h', "Chapter 5 Buyer's Responsibilities"),
    ('p', '5.1 Provide the workshop floor (load capacity ≥ 5 t/m², flatness ±3 mm/2 m), compressed air (0.5–0.7 MPa) and a '
          'three-phase 380 V power supply within 3 m of the equipment.'),
    ('p', '5.2 Carry out the machine-side signal modification and coordinate the machine tool manufacturer, completed before '
          'the equipment arrives.'),
    ('p', '5.3 Provide 50 blank samples of each model for commissioning, and 200 parts per model for the FAT.'),
    ('p', '5.4 Workshop network: provide a network cable connection to the equipment PLC and access to the MES server.'),
    ('h', 'Chapter 6 Acceptance'),
    ('p', "6.1 Factory acceptance test (FAT): at the Supplier's factory, using blanks provided by the Buyer and simulated "
          'machine signals; 4 hours of continuous dry run and loaded run, checking cycle time, pick success rate and safety '
          'functions.'),
    ('p', "6.2 Site acceptance test (SAT): at the Buyer's site, continuous production for 72 hours; cycle time, OEE and pick "
          'success rate meet Chapter 4, and no class A issue remains open. Issue classes: A (affects safety or prevents '
          'production), B (affects efficiency), C (appearance and documentation).'),
    ('p', '6.3 Up to 5 class C issues may remain open at the SAT; the Supplier shall close them within 30 days.'),
    ('h', 'Chapter 7 Training and Documentation'),
    ('p', "7.1 Training: 3 days of on-site training for the Buyer's operators and maintenance staff, no more than 10 people "
          'per session.'),
    ('p', '7.2 Documentation: provide the operation manual, maintenance manual, electrical drawings, PLC program backup '
          '(excluding the encrypted source code of the robot process package) and spare parts list, in Chinese, 2 printed '
          'sets plus electronic copies.'),
    ('h', 'Chapter 8 Signatures'),
    ('p', "Buyer's project manager: Zhou Mingyuan (Equipment Dept.)  Supplier's project manager: Chen Siyuan (Delivery Dept.)"),
]

MEETINGS = {
    'Minutes_2026-04-20_Kickoff.md': """# Project Kickoff Meeting Minutes

- Time: 2026-04-20 14:00–16:00
- Place: Huaheng Suzhou plant, 3rd floor meeting room
- Attendees: Buyer: Zhou Mingyuan (Equipment Manager), Liu Yang (Process Engineer), Wang Ting (IT/MES); Supplier: Chen Siyuan (Project Manager), Zhao Lei (Electrical Engineer), Sun Hao (Robotics Engineer)

## Topics and Decisions

1. Milestones confirmed: design freeze May 10, FAT August 10, shipment August 25, SAT before October 10.
2. Machine-side signal modification: the Buyer confirms it will be done by the machine tool manufacturer (Haitian Precision), led by Zhou Mingyuan, completed by June 30 with signal test records.
3. Blank samples: Liu Yang confirms 50 samples of each model will be provided before May 15.
4. MES interface: Wang Ting asked whether the MES can get a machining complete timestamp for every part. The Supplier replied that the point list already has a machining complete counter; the timestamp is to be confirmed in the point list review.

## Action Items

| ID | Item | Owner | Due |
|---|---|---|---|
| A-01 | Submit the overall layout drawing for Buyer approval | Sun Hao | 2026-04-30 |
| A-02 | Send the machine signal exchange list to the machine tool manufacturer | Zhao Lei | 2026-04-27 |
| A-03 | Provide blank samples | Liu Yang | 2026-05-15 |
| A-04 | Submit the first draft of the point list | Zhao Lei | 2026-05-08 |
""",
    'Minutes_2026-05-09_Design_Review.md': """# Design Review Meeting Minutes

- Time: 2026-05-09 09:30–12:00
- Attendees: Buyer: Zhou Mingyuan, Liu Yang, Wang Ting, Li Jing (Quality Dept.); Supplier: Chen Siyuan, Zhao Lei, Sun Hao

## Review Decisions

1. Layout drawing V2 approved; the floor track is shifted 400 mm to the east to avoid a workshop column.
2. Point list V0.3 approved in principle; add a "Per-part machining complete timestamp" point (DB100.DBD40), confirmed by Wang Ting.
3. Vision concept approved.

## New Requests

1. **Blank code reading**: Li Jing (Quality) asked for the robot to read the QR code on each blank before picking it, and to bind the code to the machining result and upload it to the MES for quality traceability.
   Chen Siyuan (Supplier) replied that traceability is not in the scope of this phase's Technical Agreement; a quote will follow after assessing the code reader, the cycle time impact and the MES integration effort. The Buyer will reply after internal discussion.
2. **Provision for a fifth machine**: Zhou Mingyuan said the workshop may add 1 machine next year and asked for the floor track and program to allow for it. The Supplier replied that the 8 m track travel covers the fifth machine position; program extension needs a separate assessment.
3. **Magazine tiers**: Liu Yang asked to increase the magazines from 6 to 8 tiers to extend the autonomous run time. The Supplier replied that it affects the magazine structure and delivery time and needs a written change.

## Action Items

| ID | Item | Owner | Due |
|---|---|---|---|
| A-05 | Assess and quote the code-reading traceability solution | Chen Siyuan | 2026-05-20 |
| A-06 | Assess cost and delivery impact of extra magazine tiers | Sun Hao | 2026-05-16 |
| A-07 | Update layout drawing to V3 (floor track 400 mm east) | Sun Hao | 2026-05-13 |
""",
}

DAILY_REPORTS = {
    'Site_Report_2026-09-08.md': """# Site Report 2026-09-08

- Reported by: Zhao Lei  Weather: sunny  On site: 3 Supplier staff

## Done Today

- Signal commissioning between the PLC and VMC-01 and VMC-02 complete; automatic door and chuck clamp signals normal.
- Robot R1 finished teaching ZX-A01 blank picking, magazine tiers 1–3.

## Issues

- The VMC-03 chuck clamped-in-position signal is unstable and drops out occasionally; probably a loose proximity switch on the machine side. Zhou Mingyuan (Buyer) has been asked to contact the machine tool manufacturer.
- The workshop compressed air pressure drops to 0.42 MPa at the morning shift peak, below the 0.5 MPa required by the Technical Agreement, causing 2 gripper clamping force alarms.

## Plan for Tomorrow

- Signal commissioning for VMC-03 and VMC-04.
- Teaching R2.
""",
    'Site_Report_2026-09-15.md': """# Site Report 2026-09-15

- Reported by: Sun Hao  On site: 3 Supplier staff, 1 Buyer staff assisting

## Done Today

- First continuous loaded run of the whole line for 2 hours; 154 ZX-A01 parts machined.
- Measured average cycle time 47.2 s/part, short of the 45 s requirement. Main bottleneck: R1 waits about 1.8 s for the vision snapshot after picking from the magazine.

## Issues

- The machine tool manufacturer fixed the VMC-03 chuck signal; it did not recur today.
- Li Jing (Quality) asked again whether code-reading traceability could be added before the SAT. Chen Siyuan replied that the quote was submitted on May 20 and the Buyer has not confirmed it yet.
- The compressed air problem persists and is logged in the issue log (P-007).

## Plan for Tomorrow

- Optimize vision trigger timing to take the snapshot while the robot is moving; target cycle time 44 s.
""",
    'Site_Report_2026-09-22.md': """# Site Report 2026-09-22

- Reported by: Zhao Lei

## Done Today

- Vision trigger timing optimized; average cycle time over 3 hours of continuous running is 43.6 s/part.
- 996 of 1000 picks successful, a success rate of 99.6%.
- ZX-A02 changeover test took 26 minutes, over the 20-minute requirement; most of the time goes into TCP calibration after the gripper quick change.

## Issues

- Liu Yang (Buyer) verbally asked for a per-shift output report page on the HMI; Zhao Lei replied that it must first be confirmed whether this is in scope.
- The Buyer plans to start the SAT on October 8.

## Plan for Tomorrow

- Optimize the changeover procedure: store TCP data for both grippers in advance to skip on-site calibration.
""",
}

POINTS = [
    ['No.', 'Point name', 'PLC address', 'Data type', 'Direction', 'Description'],
    [1, 'Line running state', 'DB100.DBW0', 'Int', 'PLC→MES', '0 stopped, 1 auto, 2 manual, 3 alarm'],
    [2, 'Current model', 'DB100.DBW2', 'Int', 'PLC→MES', '1 ZX-A01, 2 ZX-A02'],
    [3, 'Shift output', 'DB100.DBD4', 'DInt', 'PLC→MES', 'Good parts in the current shift'],
    [4, 'Total output', 'DB100.DBD8', 'DInt', 'PLC→MES', 'Cumulative since start of production'],
    [5, 'Current cycle time', 'DB100.DBD12', 'Real', 'PLC→MES', 'Cycle time of the last part, in s'],
    [6, 'Average cycle time', 'DB100.DBD16', 'Real', 'PLC→MES', 'Average cycle time in the current shift, in s'],
    [7, 'R1 state', 'DB100.DBW20', 'Int', 'PLC→MES', 'Robot 1 running state'],
    [8, 'R2 state', 'DB100.DBW22', 'Int', 'PLC→MES', 'Robot 2 running state'],
    [9, 'Current alarm code', 'DB100.DBW24', 'Int', 'PLC→MES', 'See alarm code table'],
    [10, 'Blanks left in magazine', 'DB100.DBW26', 'Int', 'PLC→MES', 'Number of blanks remaining'],
    [11, 'Free slots in finished-part magazine', 'DB100.DBW28', 'Int', 'PLC→MES', ''],
    [12, 'VMC-01 to 04 state', 'DB100.DBW30-36', 'Int', 'PLC→MES', 'One word per machine'],
    [13, 'Per-part machining complete timestamp', 'DB100.DBD40', 'DInt', 'PLC→MES', 'Unix seconds, added at the 2026-05-09 design review'],
    [14, 'MES production enable', 'DB101.DBX0.0', 'Bool', 'MES→PLC', 'Sent by the MES; when False the line stops after the current part'],
]

ISSUES = [
    ['ID', 'Date', 'Description', 'Class', 'Responsible party', 'Owner', 'Status', 'Planned close'],
    ['P-001', '2026-08-06', 'Safety door lock gives occasional false alarms at FAT', 'A', 'Supplier', 'Zhao Lei', 'Closed', '2026-08-09'],
    ['P-002', '2026-08-08', 'FAT cycle time 46.5 s not met (simulated machine signals)', 'B', 'Supplier', 'Sun Hao', 'Closed', '2026-08-10'],
    ['P-003', '2026-09-03', 'Floor track base dented in 2 places on arrival', 'C', 'Supplier', 'Chen Siyuan', 'Closed', '2026-09-10'],
    ['P-004', '2026-09-05', 'Machine-side signal modification late: planned for June 30, actually completed September 5', 'B', 'Buyer', 'Zhou Mingyuan', 'Closed', '2026-09-05'],
    ['P-005', '2026-09-08', 'VMC-03 chuck clamped-in-position signal unstable', 'A', 'Buyer (machine tool manufacturer)', 'Zhou Mingyuan', 'Closed', '2026-09-14'],
    ['P-006', '2026-09-15', 'Site cycle time 47.2 s, short of 45 s', 'B', 'Supplier', 'Sun Hao', 'Closed', '2026-09-22'],
    ['P-007', '2026-09-15', 'Compressed air drops to 0.42 MPa at morning shift, below 0.5 MPa', 'B', 'Buyer', 'Zhou Mingyuan', 'In progress', '2026-10-05'],
    ['P-008', '2026-09-22', 'ZX-A02 changeover takes 26 minutes, over 20 minutes', 'B', 'Supplier', 'Sun Hao', 'In progress', '2026-09-30'],
    ['P-009', '2026-09-22', 'HMI operation manual lacks a changeover chapter', 'C', 'Supplier', 'Zhao Lei', 'Not started', '2026-10-15'],
    ['P-010', '2026-09-22', 'Safety fence door 3 is missing its sign', 'C', 'Supplier', 'Zhao Lei', 'Not started', '2026-10-08'],
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    write_docx(OUT / 'Equipment_Purchase_and_Installation_Contract.docx', CONTRACT)
    write_docx(OUT / 'Technical_Agreement_V1.2.docx', TECH_AGREEMENT)
    write_xlsx(OUT / 'Point_List_V0.4.xlsx', 'OPC UA Points', POINTS)
    write_xlsx(OUT / 'Issue_Log.xlsx', 'Issue Log', ISSUES)
    for name, text in {**MEETINGS, **DAILY_REPORTS}.items():
        (OUT / name).write_text(text, encoding='utf-8')
    print(f'Wrote {len(list(OUT.iterdir()))} files to {OUT}')


if __name__ == '__main__':
    main()
