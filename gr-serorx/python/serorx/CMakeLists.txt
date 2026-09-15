include(GrPython)
if(NOT PYTHONINTERP_FOUND)
    return()
endif()

gr_python_install(FILES __init__.py grx_client.py grx_decode.py grx_source.py qt_msg_log.py
    DESTINATION ${GR_PYTHON_DIR}/gnuradio/serorx)
gr_python_install(DIRECTORY proto adsb DESTINATION ${GR_PYTHON_DIR}/gnuradio/serorx PATTERN "__pycache__" EXCLUDE)

include(GrTest)
# GnuradioConfig.cmake forces PYTHON_EXECUTABLE to the interpreter GNU Radio was built with.
# The QA tests need grpcio, which lives in the project venv, so their interpreter is a separate option.
set(SERORX_TEST_PYTHON "" CACHE FILEPATH "Interpreter for the QA tests. Empty: the GNU Radio interpreter.")
if(SERORX_TEST_PYTHON)
    set(QA_PYTHON_EXECUTABLE ${SERORX_TEST_PYTHON})
endif()
# The QA tests run against the fake receiver, which lives outside the module.
set(SERORX_FAKE_DIR ${PROJECT_SOURCE_DIR}/../fake-grx CACHE PATH "Directory holding fake_grx.py for the QA tests.")
list(APPEND GR_TEST_PYTHON_DIRS ${SERORX_FAKE_DIR})
add_custom_target(
    copy_module_for_tests ALL
    COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_CURRENT_SOURCE_DIR}
            ${PROJECT_BINARY_DIR}/test_modules/gnuradio/serorx/)

GR_ADD_TEST(qa_grx_source ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_grx_source.py)
GR_ADD_TEST(qa_proto ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_proto.py)
GR_ADD_TEST(qa_fake_grx ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_fake_grx.py)
GR_ADD_TEST(qa_grx_client ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_grx_client.py)
GR_ADD_TEST(qa_grx_decode ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_grx_decode.py)
GR_ADD_TEST(qa_crc ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_crc.py)
GR_ADD_TEST(qa_score ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_score.py)
GR_ADD_TEST(qa_modes ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_modes.py)
GR_ADD_TEST(qa_demod ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_demod.py)
GR_ADD_TEST(qa_modes_blocks ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_modes_blocks.py)
GR_ADD_TEST(qa_adsb_decoder ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_adsb_decoder.py)
GR_ADD_TEST(qa_message ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_message.py)
GR_ADD_TEST(qa_qt_msg_log ${QA_PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/qa/qa_qt_msg_log.py)
