function reports = run_all_tests()
%RUN_ALL_TESTS Run independent, consistency, JSON, and error tests.

test_root = string(fileparts(mfilename('fullpath')));
reports = struct();
reports.independent = test_core_independent();
addpath(test_root, '-begin');
reports.consistency = test_consistency();
addpath(test_root, '-begin');
reports.json = test_json_interface();
addpath(test_root, '-begin');
reports.errors = test_error_inputs();
fprintf('ALL_TESTS_PASS\n');
end
