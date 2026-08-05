

# Rajarata University of Sri Lanka 

Faculty of Applied Sciences - Department of Computing 

# **Project– ICT 3411 & COM 3405** 

# **Section 1** 

|Team Name:|Quintrix|
|---|---|
|Project title:|Intelligent-Tiered Storage Optimization for Smart Surveillance<br>Systems using Context Aware Anomaly Detection|
|Name of the<br>Supervisor/s:|Ms. A.K.N.L. Aththanagoda|



# **Section 2** 

# **Functional  Requirements to be deployed:** 

|Functional Requirement<br>Number|FR01|
|---|---|
|Function Name|User Registration|
|Description|The system shall allow new users to register an account.|
|Input|Username, full name, password|
|Process|Validate username uniqueness in database → Create user<br>record|
|Output|Success confirmation or error message if username exist.|



|Functional Requirement<br>Number|FR02|
|---|---|
|Function Name|Password Strength Validation|
|Description|The system shall validate password strength during<br>registration.|
|Input|Password string|



|Process|Check minimum length (8 characters), uppercase, lowercase,<br>numbers, special characters→Calculate strength level|
|---|---|
|Output|Real-time color-coded feedback (red for weak, yellow for<br>medium, green for strong)|



|Functional<br>Requirement Number|FR03|
|---|---|
|Function Name|User Account Approval|
|Description|The system shall require administrator approval for new<br>accounts.|
|Input|New user registration request|
|Process|Send notification to System Administrator → Administrator<br>reviews and approves/rejects|
|Output|User account status updated to 'active' or 'rejected', notification<br>sent to user|



|Functional<br>Requirement Number|FR04|
|---|---|
|Function Name|User Login|
|Description|The system shall allow registered users to login.|
|Input|Username andpassword|
|Process|Retrieve user record from database → Compare credentials|
|Output|Access granted with redirect to dashboard or error message<br>displayed|



|Functional Requirement<br>Number|FR05|
|---|---|
|Function Name|User Authentication|
|Description|The system shall authenticate user credentials securely.|
|Input|Enteredpassword,storedpassword hash|



1 

|Process|Hash entered password → Compare with stored hash →<br>Generate session token if match|
|---|---|
|Output|Session token stored for user session or authentication<br>failure message|



|Functional Requirement<br>Number|FR06|
|---|---|
|Function Name|User Logout|
|Description|The system shall allow users to logout securely.|
|Input|Logout request from authenticated user|
|Process|Invalidate session token → Clear user session data|
|Output|User redirected to loginpage,session terminated|



|Functional Requirement<br>Number|FR07|
|---|---|
|Function Name|Role-Based Access Control|
|Description|The system shall enforce access control based on user roles.|
|Input|User role(System Administrator,SecurityOperator,User)|
|Process|Query user role from database → Check permissions for<br>requested resource → Grant or denyaccess|
|Output|Access granted to authorized features or access denied<br>message|



**Video Processing and Analysis** 

2 

|Functional Requirement Number|FR 08|
|---|---|
|Function Name|Video Upload and Project Management|
|Description|The system shall allow users to upload video<br>files and createprojects for analysis.|
|Input|Video file(MP4,AVI,MOV,MKV)|
|Process|User uploads video → System creates a<br>project → Metadata is stored → Job ID<br>assigned|
|Output|Project ID and Job ID forprocessing|
|Business Rules / Other considerations|File type and size limits must be enforced|



|Functional Requirement<br>Number|FR09|
|---|---|
|Function Name|Video Segmentation|
|Description|The system shall segment uploaded videos into fixed-<br>duration clips.|
|Input|Uploaded video file|
|Process|Split video into 15-second segments → Create segment<br>records with index, timestamps|
|Output|Multiple segment records stored in database with<br>references to parent project|



|Functional Requirement<br>Number|FR10|
|---|---|
|Function Name|Motion Detection|
|Description|The system shall detect motion in video segments.|
|Input|Video segment frames|
|Process|Analyze frames for motion using frame differencing →<br>Filter static frames|
|Output|Motion detected flag (true/false), segments marked for<br>further processing or discarded|



|Functional Requirement Number|FR 11|
|---|---|
|Function Name|Object Detection using YOLOv8|
|Description|The system shall detect objects in video<br>frames using the YOLOv8 model.|
|Input|Motion-detected video frames|
|Process|YOLOv8 inference → Extract labels,<br>confidence, bounding boxes|
|Output|JSON array ofdetected objects|



3 

|Functional Requirement<br>Number|FR15||
|---|---|---|
|Function Name|Significance|Score Calculation|
|Description|The system<br>segment.|shall calculate a significance score for each|
|Input|Detected obj<br>context|ects, recognized actions, timestamp, location|
|Process|Evaluate con<br>algorithm→|text-aware factors → Apply weighting<br>Calculate Ssig|
|Output|Significance|score (Ssig) value stored withsegmentrecord|
|Business Rules / Other con|siderations|Critical objects trigger significance score<br>boost|



|Functional Requirement Number|FR 12|
|---|---|
|Function Name|Action Recognition using X3D-S|
|Description|The system shall classify human actions in<br>video segments.|
|Input|Video segments (minimum 16 frames)|
|Process|Apply X3D-S model → Generate action<br>predictions|
|Output|Top-5 action predictions with confidence<br>scores|



|Functional Requirement Number|FR 13|
|---|---|
|Function Name|Scene Sentiment Analysis|
|Description|The system shall analyze scene sentiment to<br>determine threat level.|
|Input|Video frames|
|Process|Apply MobileNetV3 → Compute sentiment<br>score→Assign threat level|
|Output|Sentiment score, sentiment label, threat<br>level|



|Functional Requirement Number|FR 14|
|---|---|
|Function Name|SsigPrioritization Engine|
|Description|The system shall compute a normalized<br>significance score for each video segment.|
|Input|Objects, actions, sentiment, context data|
|Process|Apply scoring formula → Apply contextual<br>modifiers|
|Output|Significance score (0.0-1.0)|
|BusinessRules / Otherconsiderations|Contextmodifiers affectfinalscore|



4 

|Functional Requirement<br>Number|FR16|
|---|---|
|Function Name|Metadata Generation|
|Description|The system shall generate structured metadata for analyzed<br>segments.|
|Input|Processingresults(objects,actions,scores,timestamps)|
|Process|Extract relevant information → Format into structured<br>metadata → Store in database|
|Output|Searchable metadata record containingall analysis results|



|Functional Requirement<br>Number|FR17|
|---|---|
|Function Name|Event Extraction|
|Description|The system shall extract significant events from analyzed<br>segments.|
|Input|Analyzed segments with significance scores|
|Process|Compare Ssig against threshold → Extract qualifying<br>segments as events|
|Output|Event records created with timestamp, objects, actions, tier,<br>and significance score|



# **Tiered Storage Management** 

|Functional Requirement Number|FR 18|
|---|---|
|Function Name|Tiered Storage Execution|
|Description|The system shall process video segments<br>according to their assigned storage tier.|
|Input|Video segment and assigned tier|
|Process|Apply FFmpeg rules based on tier|
|Output|Stored video or keyframes based on tier|



|Functional Requirement<br>Number|FR19|
|---|---|
|Function Name|Storage Tier Assignment|
|Description|The system shall assign video segments to appropriate<br>storage tiers.|
|Input|Segment with calculated significance score (Ssig)|
|Process|Compare Ssig against threshold_high and threshold_low →<br>Assign tier (High/Medium/Low)|
|Output|Storage tier assigned to segment, tier-specific policy applied|



5 

|Functional Requirement<br>Number|FR20|
|---|---|
|Function Name|High-Priority Storage|
|Description|The system shall store high-priority segments in original<br>quality.|
|Input|Video segment with Ssig>threshold_high|
|Process|Store video in original resolution and frame rate → Set<br>retention policy|
|Output|Video stored in high-priority tier with full quality,<br>indefinite retention|



|Functional Requirement<br>Number|FR21|
|---|---|
|Function Name|Video Transcoding for Medium-Priority Storage|
|Description|The system shall transcode medium-priority segments to<br>reduced quality.|
|Input|Video segment with threshold_low<Ssig ≤ threshold_high|
|Process|Apply H.264 codec compression → Reduce<br>resolution/bitrate→Delete original|
|Output|Compressed video stored in medium-priority tier, original<br>discarded, storage space saved|



|Functional Requirement<br>Number|FR22|
|---|---|
|Function Name|Keyframe Extraction for Low-Priority Storage|
|Description|The system shall extract keyframes from low-priority<br>segments.|
|Input|Video segment with Ssig ≤ threshold_low|
|Process|Extract representative keyframes → Store keyframes and<br>metadata→Delete full video|
|Output|Keyframes and metadata retained, full video discarded,<br>minimal storage used|



|Functional Requirement Number|FR 23|
|---|---|
|Function Name|Real-Time Hazard Threat Assessment|
|Description|The system shall classify immediate hazards<br>based on detected objects and sentiment.|
|Input|Object detection and sentiment results|
|Process|Evaluate triggers → Assign hazard level|
|Output|Hazard classification level|



6 

|Functional Requirement Number|FR 24|
|---|---|
|Function Name|User Authentication and Authorization|
|Description|The system shall authenticate users and<br>manage access roles.|
|Input|Username and password|
|Process|Validate credentials → Generate access<br>token→Assign role|
|Output|Authentication token|
|Business Rules / Other considerations|Admin<br>approval<br>required<br>for<br>new<br>registrations|



|Functional Requirement Number|FR 25|
|---|---|
|Function Name|Real-Time Analytics Dashboard|
|Description|The system shall display analytics and<br>intelligence insights.|
|Input|System metrics and analysis results|
|Process|Aggregate metrics→Render visualizations|
|Output|Analytics dashboard view|



|Functional Requirement Number|FR 26|
|---|---|
|Function Name|Forensic Archive Search|
|Description|The system shall allow searching archived<br>events and playback.|
|Input|Search filters (object, action, tier, time)|
|Process|Query archive→Retrieve matching results|
|Output|Search results with video and metadata|



|Functional Requirement Number|FR 27|
|---|---|
|Function Name|Project and Segment Deletion|
|Description|The system shall allow authorized users to<br>delete projects and segments.|
|Input|Project ID|
|Process|Delete database records→Remove files|
|Output|Project removed successfully|
|Functional Requirement Number|FR 28|
|FunctionName|VideoTechnical MetadataExtraction|



7 

|Description|The system shall extract technical metadata<br>from videos.|
|---|---|
|Input|Video file|
|Process|Analyze video properties → Generate<br>metadata|
|Output|Technical metadata JSON|



|Functional Requirement Number|FR 29|
|---|---|
|Function Name|Asynchronous Video Processing|
|Description|The<br>system<br>shall<br>process<br>videos<br>asynchronously.|
|Input|Video job request|
|Process|Assign job ID → Execute in background<br>thread|
|Output|Job status updates|



|Functional Requirement Number|FR 30|
|---|---|
|Function Name|Processing Log Stream|
|Description|The system shall stream real-time processing<br>logs.|
|Input|Processing events|
|Process|Capture events→Stream logs|
|Output|Live log feed|



|Functional Requirement Number|FR 31|
|---|---|
|Function Name|Storage Efficiency Metrics|
|Description|The system shall calculate storage savings.|
|Input|Original size and tiered size|
|Process|Calculate savings→Aggregate statistics|
|Output|Storage efficiency metrics|



|Functional Requirement Number|FR 32|
|---|---|
|Function Name|Interactive Segment Timeline|
|Description|The system shall display video segments in a<br>timeline view.|
|Input|Processed segments|
|Process|Render timeline→Enable interaction|
|Output|Interactive segment timeline|
|Functional Requirement Number|FR 33|
|Function Name|Automatic Thumbnail Generation|



8 

|Description|The system shall generate thumbnails for<br>video segments.|
|---|---|
|Input|Video segment|
|Process|Extract keyframe→Save thumbnail|
|Output|JPEG thumbnail image|



# **Search and Retrieval** 

|Functional<br>Requirement Number|FR34|
|---|---|
|Function Name|Timeline-Based Search|
|Description|The system shall allow searchingbytime range.|
|Input|Start date/time,end date/time|
|Process|Query database for segments within time range → Sort by<br>timestamp|
|Output|List of matchingsegments with timestamps and metadata|



|Functional<br>Requirement<br>Number|FR35|
|---|---|
|Function Name|MetadataQuerySearch|
|Description|The system shall allow searchingbymetadata filters.|
|Input|Object types,action types,location,significance score range|
|Process|Build query with filters → Execute database search →<br>Retrieve matchingrecords|
|Output|Filtered search results with relevant metadata displayed|



|Functional<br>Requirement<br>Number|FR36|
|---|---|
|Function Name|Importance-Based Search|
|Description|The system shall allow filtering by storage tier or significance score.|
|Input|Storage tier selection or significance score threshold|



9 

|Process|Filter segments by tier or Ssig value → Sort by relevance|
|---|---|
|Output|List of segments matching importance criteria, sorted by significance|



|Functional<br>Requirement<br>Number|FR37|
|---|---|
|Function Name|Search Results Display|
|Description|The system shall display search results in a user-friendly<br>format.|
|Input|Search results from query|
|Process|Format results with thumbnail, timestamp, objects, actions,<br>score→Paginate|
|Output|Paginated list displayed with clickable results for detailed<br>view|



|Functional Requirement<br>Number|FR38|
|---|---|
|Function Name|Stored Footage Playback|
|Description|The system shall allowplayback of stored video footage.|
|Input|Selected video segment from search results|
|Process|Stream video from storage → Load in embeddedplayer|
|Output|Videoplayback with controls(play, pause,seek,volume)|



|Functional Requirement<br>Number|FR39|
|---|---|
|Function Name|Metadata Viewing|
|Description|The system shall allow viewing detailed metadata for<br>segments.|
|Input|Selected video segment|
|Process|Retrieve all associated metadata from database →<br>Format for display|
|Output|Detailed metadata displayed (objects, actions, scores,<br>timestamps,tier)|



10 

|Functional Requirement Number|FR 40|
|---|---|
|Function Name|Ensemble ML Analysis|
|Description|The system shall execute multiple ML models<br>sequentially.|
|Input|Video data|
|Process|Run YOLO → Action → Sentiment → Fuse<br>results|
|Output|Combined inference results|



# **Alerting and Notifications** 

|Functional<br>Requirement Number|FR41|
|---|---|
|Function Name|Real-Time Alert Generation|
|Description|The system shallgenerate alerts for high-significance events.|
|Input|Segment with Ssigexceedingalert threshold|
|Process|Compare Ssig against alert threshold → Create alert record<br>with event details|
|Output|Alert record created with timestamp, event details, and<br>severity level|



|Functional<br>Requirement Number|FR42|
|---|---|
|Function Name|Alert Delivery to Security Operators|
|Description|The system shall deliver alerts to Security Operators in real-<br>time.|
|Input|Generated alert record|
|Process|Push notification to Security Operator interface → Display<br>alert|
|Output|Alert displayed in web interface with visual/audio indicators|



|Functional<br>Requirement Number|FR43|
|---|---|
|Function Name|Alert Acknowledgment|
|Description|The system shall allow Security Operators to acknowledge<br>alerts.|
|Input|Acknowledgment action from Security Operator|
|Process|Update alert status to 'acknowledged' → Log acknowledgment<br>timestamp and user|
|Output|Alert status updated, acknowledgment recorded in system logs|



11 

# **System Configuration and Administration** 

|Functional<br>Requirement Number|FR44|
|---|---|
|Function Name|System Parameter Configuration|
|Description|The system shall allow administrators to configure processing<br>parameters.|
|Input|Motion detection sensitivity, object detection thresholds, action<br>recognitionparameters|
|Process|Validate parameter values → Store in configuration → Apply to<br>subsequentprocessing|
|Output|Configuration saved, new parameters applied to future video<br>processing|



|Functional Requirement<br>Number|FR45|
|---|---|
|Function Name|Storage Threshold Configuration|
|Description|The system shall allow administrators to configure storage tier<br>thresholds.|
|Input|threshold_high value, threshold_low value|
|Process|Validate threshold values (threshold_low < threshold_high) →<br>Store configuration|
|Output|Thresholds saved and applied to segment tierassignment|



|Functional Requirement<br>Number|FR46|
|---|---|
|Function Name|Context Rules Configuration|
|Description|The system shall allow administrators to define contextual rules.|
|Input|Time-based rules, location rules, object-action combination<br>rules|
|Process|Validate rule syntax → Store rules in configuration → Apply to<br>significance calculation|
|Output|Context rules saved and used in Ssig calculation for new<br>segments|



12 

|Functional<br>Requirement Number|FR47|
|---|---|
|Function Name|Alert Threshold Configuration|
|Description|The system shall allow administrators to configure alert<br>thresholds.|
|Input|Alert threshold value,severitylevels|
|Process|Validatethreshold values→Storealertconfiguration|
|Output|Alert thresholds saved and applied to determine when alerts<br>trigger|



|Functional<br>Requirement Number|FR48|
|---|---|
|FunctionName|System Logs Viewing|
|Description|The systemshall allowadministratorsto view system logs.|
|Input|Logfilter criteria(date range,logtype,severity)|
|Process|Querylogsfromdatabase→ Filter and sort results|
|Output|Filtered log entries displayed with timestamp, message, context,<br>and type|



|Functional<br>Requirement Number|FR49|
|---|---|
|Function Name|Processing Status Monitoring|
|Description|The system shall provide real-time processing status to<br>administrators.|
|Input|System requests status update|
|Process|Query current processing queue → Calculate metrics → Format<br>dashboard data|
|Output|Dashboard displaying active projects, queue length, completed<br>segments, resource utilization (updated every 2 seconds)|



# **Data Management and Integrity** 

|Functional|FR50|
|---|---|
|RequirementNumber||
|Function Name|Project Management|
|Description|The system shall allow management of video projects.|
|Input|Project action (create, view, update, delete) with project data|



13 

|Process|Execute requested operation → Enforce referential integrity →<br>Update database|
|---|---|
|Output|Project created/updated/deleted, related segments handled<br>appropriately, confirmation message|



|Functional<br>Requirement Number|FR51|
|---|---|
|Function Name|Segment Deletion with Cascade|
|Description|The system shall handle segment deletion with cascade<br>operations.|
|Input|Segment deletion request|
|Process|Begin database transaction → Delete segment → Cascade<br>delete events,metadata,files → Commit transaction|
|Output|Segment and all related data deleted, storage files removed,<br>database consistent|



|Functional<br>Requirement Number|FR52|
|---|---|
|Function Name|Storage Space Reporting|
|Description|The system shall report current storage utilization.|
|Input|Storage report request|
|Process|Calculate storage by tier (High, Medium, Low) → Sum total<br>archive size|
|Output|Storage report displaying utilization by tier and total storage<br>used|



|Functional<br>Requirement Number|FR53|
|---|---|
|Function Name|Work Log Recording|
|Description|The system shall record all significant system operations.|
|Input|System operation (login, upload, processing, error,<br>configuration change)|
|Process|Capture operation details → Format log entry → Store in<br>database with timestamp and log type|
|Output|Log entry created and storedforauditing andmonitoring|
|Functional<br>Requirement Number|FR54|



14 

|Function Name|API Request Logging|
|---|---|
|Description|The system shall log all API requests.|
|Input|API request (method, endpoint, parameters, user identity)|
|Process|Capture request details → Log with timestamp and response<br>status→Store in database|
|Output|API request logged and queryable through administration<br>interface(last 100 events)|



|Functional Requirement Number|FR 55|
|---|---|
|Function Name|Analysis Frame Downscaling|
|Description|The system shall downscale frames for analysis.|
|Input|Video frames|
|Process|Resize frames → Preserve originals|
|Output|Optimized frames for ML analysis|



|Functional Requirement Number|FR 56|
|---|---|
|Function Name|Administrative User Control|
|Description|Admins shall manage system users and<br>approvals.|
|Input|Admin requests|
|Process|View users→Approve or reject→Update stats|
|Output|User management actions completed|



|Functional Requirement Number|FR 57|
|---|---|
|Function Name|System Event Audit Trail|
|Description|The system shall log all significant system<br>events.|
|Input|System events|
|Process|Record events→Store in audit log|
|Output|Audit trail records|



15 

|Functional Requirement Number|FR 58|
|---|---|
|Function Name|Manual Storage Tier Assignment|
|Description|Authorized users may manually override storage<br>tier.|
|Input|Project ID, Tier level|
|Process|Apply manual tier→Reprocess project|
|Output|Updated storage tier applied|



|Functional Requirement Number|FR 59|
|---|---|
|Function Name|Intelligent Recommendation Generation|
|Description|The<br>system<br>shall<br>generate<br>AI-based<br>recommendations.|
|Input|Analysis patterns|
|Process|Evaluate patterns→Generate recommendations|
|Output|AI recommendations|



|Functional Requirement Number|FR 60|
|---|---|
|Function Name|Compute Device Auto Selection|
|Description|The system shall automatically select optimal<br>compute device.|
|Input|Available hardware resources|
|Process|Detect device→Assign priority|
|Output|Selected compute device|



# **Section 03** 

# **Non-functional Requirements to be deployed:** 

|Number|01|
|---|---|
|Non-Functional Requirement|Performance|
|Objective / Benefit to the Project|1. Efficient Video Processing<br>2. Responsive API<br>3. Fast Dashboard Loading|
|Measurement|1. Video Processing Time<br>2. API Response Time<br>3. Dashboard Loading Time|
|Data to be used to evaluate|Video size, duration, resolution,<br>processing time.<br>Request/response timestamps, response<br>time.<br>Page load time, network latency.|



16 

|Number|02|
|---|---|
|Non-Functional Requirement|Scalability|
|Objective / Benefit to the Project|Support multiple concurrent projects.<br>Handle large numbers of video segments.<br>Support large video archives.|
|Measurement|Concurrent project performance.<br>Segment processing and storage capacity.<br>Storage performance as data increases.|
|Data to be used to evaluate|Number of projects, processing time, resource<br>usage.<br>Number and size of segments, storage used.<br>Archive size, storage usage, retrieval time.|



|Number|03|
|---|---|
|Non-Functional Requirement|Reliability|
|Objective / Benefit to the Project|Automatic failure recovery.<br>Complete failure logging.<br>Prevent data loss.<br>Maintain high processing success|
|Measurement|Failure detection and recovery time.<br>Log completeness.<br>Database consistency after shutdown.<br>Processing success rate|
|Data to be used to evaluate|Failure type, detection/recovery time.<br>Timestamp, error type, context.<br>Database records and transaction status.<br>Successfulandfailed processing attempts.|



|Number|04|
|---|---|
|Non-Functional Requirement|Usability|
|Objective / Benefit to the Project|Easy video upload and result viewing.<br>Real-time processing updates.<br>Consistent user interface.<br>Clear error messages.|
|Measurement|Task completion time and success rate.<br>Status update delay.<br>UI consistency testing.<br>Error message evaluation.|
|Data to be used to evaluate|Task time, success rate, user feedback.<br>State-change and display timestamps.<br>Screen layouts and navigation.<br>Error messages and user feedback.|



|Number|05|
|---|---|
|Non-Functional Requirement|Maintainability|
|Objective / Benefit to the Project|Support modular system updates.<br>Improve code readability.|



17 

||Simplify configuration changes.|
|---|---|
|Measurement|Module replacement testing.<br>Code documentation review.<br>Configuration inspection.|
|Data to be used to evaluate|Module dependencies and test results.<br>Documented functions and code review<br>results.<br>Configuration files and parameters.|



|Number|06|
|---|---|
|Non-Functional Requirement|Portability|
|Objective / Benefit to the Project|Support Windows, Linux, and macOS.<br>Enable consistent deployment through<br>containers.<br>Support platform-independent processing.|
|Measurement|Cross-platform deployment testing.<br>Container deployment testing.<br>Video processing on supported OSs|
|Data to be used to evaluate|OS versions and deployment results.<br>Container status and test results.<br>Processing results and compatibility errors.|



|Number|07|
|---|---|
|Non-Functional Requirement|Availability|
|Objective / Benefit to the Project|Maintain high system uptime.<br>Automatically recover from temporary<br>failures.<br>Minimize maintenance downtime.|
|Measurement|System uptime percentage.<br>Failure detection and recovery time.<br>Total maintenance downtime|
|Data to be used to evaluate|Uptime and downtime records.<br>Failure and recovery timestamps.<br>Maintenance start/end times.|



|Number|08|
|---|---|
|Non-Functional Requirement|Extensibility|
|Objective / Benefit to the Project|Support additional ML models.<br>Allow new storage policies.<br>Support database expansion.|
|Measurement|New ML model integration testing.<br>New storage policy testing.<br>Database schema modification testing.|
|Data to be used to evaluate|Model integration results.<br>Configuration changes and policy results.<br>Schema changes and regression test results.|



18 

|Number|09|
|---|---|
|Non-Functional Requirement|Interoperability|
|Objective / Benefit to the Project|Enable external system integration.<br>Support common video formats.<br>Enable browser-based video playback.|
|Measurement|REST API testing.<br>Video format compatibility testing.<br>Browser playback testing.|
|Data to be used to evaluate|API endpoints, methods, and responses.<br>File format, size, and processing result.<br>Browser, codec, and playback result.|



|Number|10|
|---|---|
|Non-Functional Requirement|Browser Compatibility|
|Objective / Benefit to the Project|Support Chrome, Firefox, Safari, and Edge.<br>Ensure consistent video playback.<br>Maintain consistent UI behavior.|
|Measurement|Cross-browser functional testing.<br>Cross-browser video playback testing.<br>Cross-browser UI testing.|
|Data to be used to evaluate|Browser versions and test results.<br>Browser, video format, playback result.<br>Screenshots and UIdifferences.|



|Number|11|
|---|---|
|Non-Functional Requirement|Security|
|Objective / Benefit to the Project|Prevent unauthorized access.<br>Monitor unauthorized attempts.<br>Control new user activation.|
|Measurement|Authentication testing.<br>Unauthorized access logging.<br>User approval workflow testing.|
|Data to be used to evaluate|Credentials, endpoints, access results.<br>Timestamp, user, endpoint, access status.<br>Registration and approval status.|



|Number|12|
|---|---|
|Non-Functional Requirement|Data Integrity|
|Objective / Benefit to the Project|Maintain project-segment relationships.<br>Prevent incomplete database updates.<br>Prevent orphaned records.|
|Measurement|Database integrity testing.<br>Transaction commit/rollback testing.<br>Orphan record checking.|
|Data to be used to evaluate|Project/segment IDs and relationships.<br>Transaction status and database state.<br>Deleted and remaining records.|



19 

|Number|13|
|---|---|
|Non-Functional Requirement|Logging and Monitoring|
|Objective / Benefit to the Project|Monitor API activities.<br>Track video processing stages.<br>Support system event monitoring.<br>Simplify error diagnosis.|
|Measurement|API log verification.<br>Processing log verification.<br>Log search testing.<br>Critical error log testing.|
|Data to be used to evaluate|API timestamps, endpoints, users, status.<br>Processing stages and processing time.<br>System events and query results.<br>Error type, timestamp, and context.|



# **Section 04 - Research Design** 

The research component of this project focuses on developing an ensemble machine learning pipeline that integrates object detection, action recognition, and sentiment analysis to enable intelligent tiered storage optimization for surveillance footage. The workflow below illustrates the complete pipeline from raw video input to storage tier assignment and alert generation. 

# 4.1 Data/Input Use 

|Name of the workflow unit|Video Data Acquisition|
|---|---|
|Objective of the Function|Obtain and prepare video data for ML<br>analysis and storage optimization|
|Input|Raw surveillance video files (MP4, AVI,<br>MOV, MKV formats)|
|Process in Brief|1. User uploads video file through web<br>interface<br>2. System creates project and assigns Job ID<br>(FR08)<br>3. System extracts technical metadata<br>(FR28) including resolution, frame rate,<br>codec, duration<br>4. Video is split into 15-second fixed-<br>duration segments (FR09)|



20 

||5. Segment metadata (index, timestamps,<br>parent project reference) is stored in<br>database|
|---|---|
|Output|Project record with Project ID, Job ID, and<br>segmented video clips ready for ML<br>analysis|
|Business rules/constraints/Assumptions if<br>available|• File size limit enforced for uploads<br>• Only MP4, AVI, MOV, MKV formats<br>accepted<br>• Each segment is exactly 15 seconds in<br>duration<br>• Minimum 16 frames required for action<br>recognition (FR12)<br>• Original video preserved for high-priority<br>segments|



# 4.2 Data Preprocessing 

|Name of the workflow unit|Frame Preprocessing and Downscaling|
|---|---|
|Objective of the function|Prepare video frames for efficient and<br>accurate ML inference|
|Input|Raw video frames from segmented clips|
|Process in Brief|1. Extract frames from each 15-second<br>video segment<br>2. Apply frame downscaling for ML<br>analysis while preserving originals (FR55)<br>3. Normalize pixel values to range [0, 1] or<br>[-1, 1] for model compatibility<br>4. Convert frames to RGB format as<br>required by ML models<br>5. Ensure minimum 16 frames are available<br>for X3D-S action recognition|
|Output|Optimized frame tensors ready for ML<br>analysis; original frames preserved for<br>storage|
|Business rules/constraints/Assumptions if<br>available|• Frame downscaling optimized for<br>YOLOv8 input dimensions (640x640)<br>• Minimum 16 frames required for X3D-S<br>(FR12)<br>• Original frames stored separately for high-<br>priority segments|



21 

# 4.3 Model / Algorithm 

|Name of the workflow unit|Ensemble Machine Learning Analysis<br>(FR40)|
|---|---|
|Objective of the Function|Execute multiple ML models sequentially to<br>extract comprehensive scene understanding<br>including objects, actions, and sentiment|
|Input|Preprocessed video frames and segments|
|Process in Brief|1. YOLOv8 Object Detection (FR11):<br>Process motion-detected frames through<br>YOLOv8 → Extract labels, confidence<br>scores, and bounding boxes<br>2. X3D-S Action Recognition (FR12):<br>Process video segments (min 16 frames)<br>through X3D-S → Generate top-5 action<br>predictions with confidence scores<br>3. MobileNetV3 Scene Sentiment Analysis<br>(FR13): Process video frames through<br>MobileNetV3 → Compute sentiment score,<br>label, and threat level<br>4. Ensemble Fusion: Combine results from<br>all models into a unified output structure.|
|Output|Combined inference results: JSON array of<br>detected objects, action predictions with<br>confidence scores, and sentiment/threat<br>classification|
|Business rules/constraints/Assumptions if<br>available|• YOLOv8 requires minimum 640x640<br>input resolution<br>• X3D-S requires minimum 16 consecutive<br>frames<br>• Action recognition uses full fine-tuning<br>with 83.1% best validation accuracy<br>• MobileNetV3 outputs sentiment scores<br>ranging from 0.0 to 1.0|



# 4.4 Model Evaluation 

|Name of the workflow unit|Model Performance Evaluation|
|---|---|
|Objective of the Function|Assess and validate the performance of<br>individual ML models and the overall<br>ensemble pipeline|
|Input|Model predictions and ground truth labels<br>from validation dataset|
|Process in Brief|1. Split dataset into training (80%) and<br>validation (20%) sets<br>2. Train YOLOv8 on COCO/surveillance<br>dataset with object detection metrics<br>3. Train X3D-S using full fine-tuning<br>approach over 15 epochs|



22 

||4. Evaluate MobileNetV3 sentiment<br>classification performance<br>5. Compute overall ensemble accuracy on<br>test data|
|---|---|
|Output|Performance metrics including accuracy,<br>precision, recall, F1-score, confusion matrix|
|Business rules/constraints/Assumptions if<br>available|• Action Recognition (X3D-S): 83.1% best<br>validation accuracy<br>• Overall Test Accuracy: 79.0%<br>• Validation gap of 4.0% indicates<br>acceptable generalization<br>• Cross-validation used to prevent<br>overfitting|



# 4.5 Output Application 

|Name of the workflow unit|Significance Scoring and Tiered Storage<br>Assignment|
|---|---|
|Objective of the Function|Transform ML outputs into actionable<br>intelligence through significance-based<br>storage optimization<br>|
|Input|Combined inference results (objects,<br>actions, sentiment scores, timestamps,<br>location context)|
|Process in Brief|1. Ssig Prioritization Engine (FR14):<br>Compute normalized significance score<br>(0.0-1.0) using:<br>• Detected objects with critical object boost<br>• Recognized actions with confidence scores<br>• Sentiment threat level assessment<br>• Contextual modifiers (time, location,<br>object-action combinations)<br>2. Storage Tier Assignment (FR19):<br>Compare Ssig against thresholds:<br>• HIGH: Ssig > threshold_high<br>(configurable, default 0.7)<br>• MEDIUM: threshold_low < Ssig ≤<br>threshold_high<br>• LOW: Ssig ≤ threshold_low<br>3. Tiered Storage Execution (FR18): Apply<br>FFmpeg rules per tier<br>4. Event Extraction (FR17): Extract<br>segments exceeding alert threshold as<br>events|
|Output|• Storage tier assignment with tier-specific<br>policies applied<br>• Event records with timestamp, objects,<br>actions, tier, and significance score|



23 

