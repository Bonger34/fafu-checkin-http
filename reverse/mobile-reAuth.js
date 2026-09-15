var DEFAULT_SALT = "rjBFAaHsNkKAhpoi";
// 这个是设计到登陆操作的统一js
$(function () {
	$("input").attr("autocomplete","off");
	if (window.parent != window){
		window.top.location.href = window.top.location.href;
	}
	initView();//初始化页面

	$("input").on("input",function(){
		$(this).parent().removeClass("required");
	});
	
	$(".submit_btn").on("click",function(){
		doLogin(this);
	});

    $(".submit_btn_otpBind").on("click",function(){
        doOtpBindLogin(this);
    });

    // $('.dialog_btn_cancel').click(function () {
    //     //解绑取消按钮
    //     $("#checkPhoneCode_mobileOtp").val("");
    //     $('.dialog, .mask').hide();
    // });

    $('#optMobileBindSecret').click(function() {
        $("#optMobileBindQrCodeDiv").hide();
        $("#optMobileBindSecretDiv").show();
        $("#optMobileBindSecret").addClass("otp_bind_check");
        $("#optMobileBindQrCode").removeClass("otp_bind_check");
        $("#optMobileBindQrCode").css("color","#86909C");
        $("#optMobileBindSecret").css("color","#165DFF");
        $("#optMobileBindSecret").css("border-color","transparent");
        $("#optMobileBindQrCode").css("border-left","1px solid #EDEFF2");
        $("#optMobileBindQrCode").css("border-bottom","1px solid #EDEFF2");
    });

    $('#optMobileBindQrCode').click(function() {
        $("#optMobileBindQrCodeDiv").show();
        $("#optMobileBindSecretDiv").hide();
        $("#optMobileBindQrCode").addClass("otp_bind_check");
        $("#optMobileBindSecret").removeClass("otp_bind_check");
        $("#optMobileBindSecret").css("color","#86909C");
        $("#optMobileBindQrCode").css("color","#165DFF");
        $("#optMobileBindQrCode").css("border-color","transparent");
        $("#optMobileBindSecret").css("border-right","1px solid #EDEFF2");
        $("#optMobileBindSecret").css("border-bottom","1px solid #EDEFF2");
    });
    startTime("mobileClockTime");

    //使用监听方法,改变otp页面展示的系统时间
    onPageVisibility({
        show:function(){
            //调用接口查询系统时间
            $.ajax({
                type: "POST",
                url: contextPath+"/systemTime",
                data:{},
                success: function (data) {
                    $("#mobileClockTime").html(data.systemTime);
                    $("#mobileOtpBindClockTime").html(data.systemTime);
                }
            });
        },
        hide:function(){
        }
    });

    var app_service=reAuthParams.service;
    if(app_service != '' && app_service !=undefined){
        utils.setUrlParam("logoutA","?service",encodeURIComponent(app_service));
    }else{
        if(serverPrefix!=undefined && serverPrefix) {
            utils.setUrlParam("logoutA", "?service", encodeURIComponent(serverPrefix));
        }

    }
});

//校验验证码.
function checkMobileReAuthOtpPhoneCode() {
    var checkPhoneCode = $("#checkPhoneCode_mobileOtp").val();
    $.ajax({
        type: "POST",
        url: contextPath+"/otp/checkDynamicCodeByReAuthOtp.do",
        dataType: "json",
        data: {
            checkPhoneCode:checkPhoneCode,
        },
        success: function (data) {
            if(data.errCode == 1){
                //手机号校验成功，展示otp绑定二维码
                $.ajax({
                    type: "POST",
                    url: contextPath+"/otp/getOtpToken.do",
                    dataType: "json",
                    data: {
                        otpSign:data.data,
                    },
                    success: function (data) {
                        if(data.code == 1){
                            $('.dialog, .mask').hide();
                            //展示otp绑定二维码
                        }else{
                            utils.alertBox(data.message);
                        }
                    }
                });
            }else{
                utils.alertBox(data.errMsg);
            }
        }
    });
}

/* 监听页面显示隐藏 */
function onPageVisibility(functions){
    var _t = {};
    var onShowCall = function(){
        if(!functions || !functions.show){
            return;
        }
        window.clearTimeout(_t.showTime);
        _t.showTime = window.setTimeout(function(){
            functions.show();
        },100);
    }
    var onHideCall = function(){
        if(!functions || !functions.hide){
            return;
        }
        window.clearTimeout(_t.hideTime);
        _t.hideTime = window.setTimeout(function(){
            functions.hide();
        },100);
    }
    document.addEventListener('visibilitychange', function(){
        var visibility = document.visibilityState;
        if(visibility == 'visible'){
            onShowCall();
        }else if(visibility == 'hidden'){
            onHideCall();
        }
    });

    window.addEventListener("pageshow", function(){
        onShowCall()
    }, false);

    window.addEventListener("pagehide", function(){
        onHideCall();
    }, false);
}

//页面内容初始化
function initView(){
	//切换方式清空输入框
	$("#password,#passwordEncrypt,#dynamicCode,#answer1,#answer2").val("");
    // 可选校验关闭提示信息
    if (reAuthParams.optionalValidTip == "1") {
        $("#optionalValidTipId").show();
    } else {
        $("#optionalValidTipId").hide();
    }
	if(reAuthParams.ipAnomaly==0 && reAuthParams.reAuthType == ""){
		reAuthParams.reAuthType = "2";
		$("#reAuthDec").html(reAuthParams.reAuthDec0);
		$("#userNameDiv").show();
		$("#pwdType").show();
		$("#dynamicType").hide();
		$("#questionDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
		$("#cllt").val("userNameLogin");
		//提交前必填属性校验
	    $("#casLoginForm").submit(checkRequired);
	}else if(reAuthParams.ipAnomaly > 0){
		reAuthParams.reAuthType = "2";
		$("#reAuthDec").html(reAuthParams.reAuthDec1);
		$("#userNameDiv").show();
		$("#pwdType").show();
		$("#dynamicType").hide();
		$("#questionDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
		$("#cllt").val("userNameLogin");
		//提交前必填属性校验
	    $("#casLoginForm").submit(checkRequired);
	}else if(reAuthParams.reAuthType == "2" || reAuthParams.reAuthType == ""){
		//用户名密码登录
		reAuthParams.reAuthType = "2";
		$("#reAuthDec").html(reAuthParams.reAuthDec2);
		$("#userNameDiv").show();
		$("#pwdType").show();
		$("#dynamicType").hide();
		$("#questionDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
		$("#cllt").val("userNameLogin");
		//提交前必填属性校验
	    $("#casLoginForm").submit(checkRequired);
	}else if(reAuthParams.reAuthType == "3"){
		//手机动态码登录
		countDownButton($("#getDynamicCode"), 0);
		$("#reAuthDec").html(reAuthParams.reAuthDec3);
		$("#userNameDiv").show();
		$("#dynamicType").show();
		$("#pwdType").hide();
		$("#questionDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
		$("#cllt").val("dynamicLogin");
		//提交前必填属性校验
	    $("#casLoginForm").submit(checkRequired);
	}else if(reAuthParams.reAuthType == "4"){
		//企业微信动态码登录
		countDownButton($("#getDynamicCode"), 0);
		$("#reAuthDec").html(reAuthParams.reAuthDec4);
		$("#userNameDiv").show();
		$("#dynamicType").show();
		$("#pwdType").hide();
		$("#questionDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
		$("#cllt").val("dynamicLogin");
		//提交前必填属性校验
	    $("#casLoginForm").submit(checkRequired);
	}else if(reAuthParams.reAuthType == "5"){
		//今日校园动态码登录
		countDownButton($("#getDynamicCode"), 0);
		$("#reAuthDec").html(reAuthParams.reAuthDec5);
		$("#userNameDiv").show();
		$("#dynamicType").show();
		$("#pwdType").hide();
		$("#questionDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
		$("#cllt").val("dynamicLogin");
		//提交前必填属性校验
	    $("#casLoginForm").submit(checkRequired);
	}else if(reAuthParams.reAuthType == "7"){
		//回答安全问题
		$("#reAuthDec").html(reAuthParams.reAuthDec7);
		$("#questionDiv").show();
		$("#userNameDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
		getRandomQuestion();
	}else if(reAuthParams.reAuthType == "9"){
        //QQ
        $("#reAuthDec").html(reAuthParams.reAuthDec9);
        $("#combinedQqDiv").show();
        $("#questionDiv").hide();
        $("#userNameDiv").hide();
        $("#combinedWxDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
    }else if(reAuthParams.reAuthType == "10"){
        //OTP
        $("#reAuthDec").html(reAuthParams.reAuthDec10);
        $("#otpDiv").show();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").show();
        $("#optUpdateInfoTips").show();
        $("#combinedQqDiv").hide();
        $("#questionDiv").hide();
        $("#userNameDiv").hide();
        $("#combinedWxDiv").hide();
        $("#faceAuthShowDiv").hide();
        if(reAuthParams._isBindOtp==false){
            //展示otp绑定二维码
            $('.otp_checkPhone').hide();
            $("#otpDiv").hide();
            $("#otpDivBind").show();
            // $("#optUpdateInfoTips").hide();
            if(reAuthParams._otpToken!=''){
                // var otpToken=decryptPassword(reAuthParams._otpToken,DEFAULT_SALT);
                var otpToken=atob(reAuthParams._otpToken);
                $("#otp_bind_secret").html(otpToken.substring(0,10)+"...");
                $("#otp_bind_secret_hidden").val(otpToken);
            }
            if($("#mobileOtpBindClockTime").text()==''){
                $("#mobileOtpBindClockTime").html(reAuthParams._systemTime);
            }
            startTime("mobileOtpBindClockTime");
            $("#mobileOtpBindImg").attr("src","data:image/jpeg;base64,"+reAuthParams._otpUrl);
            clearMobileOtpCode(2);
        }else{
            clearMobileOtpCode(1);
        }
    }else if(reAuthParams.reAuthType == "11"){
        //邮箱
        countDownButton($("#getDynamicCode"), 0);
        $("#reAuthDec").html(reAuthParams.reAuthDec11);
        $("#userNameDiv").show();
        $("#dynamicType").show();
        $("#pwdType").hide();
        $("#questionDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
        $("#cllt").val("dynamicLogin");
        //提交前必填属性校验
        $("#casLoginForm").submit(checkRequired);
    }else if(reAuthParams.reAuthType == "12"){
        //dingTalk
        countDownButton($("#getDynamicCode"), 0);
        $("#reAuthDec").html(reAuthParams.reAuthDec12);
        $("#userNameDiv").show();
        $("#dynamicType").show();
        $("#pwdType").hide();
        $("#questionDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
        $("#cllt").val("dynamicLogin");
        //提交前必填属性校验
        $("#casLoginForm").submit(checkRequired);
    }else if(reAuthParams.reAuthType == "13"){
        //weLink
        countDownButton($("#getDynamicCode"), 0);
        $("#reAuthDec").html(reAuthParams.reAuthDec13);
        $("#userNameDiv").show();
        $("#dynamicType").show();
        $("#pwdType").hide();
        $("#questionDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
        $("#faceAuthShowDiv").hide();
        $("#cllt").val("dynamicLogin");
        //提交前必填属性校验
        $("#casLoginForm").submit(checkRequired);
    }else if(reAuthParams.reAuthType == "6" || reAuthParams.reAuthType == "18"
        || reAuthParams.reAuthType.indexOf("real_person_plugin_") !== -1 || reAuthParams.reAuthType.indexOf("REAL_PERSON_PLUGIN_") !== -1){
        //人脸认证
        if(reAuthParams.reAuthType == "18"){
            $("#pluginId").val("REAL_PERSON_PLUGIN_TENCENT");
        }else if(reAuthParams.reAuthType == "6"){
            $("#pluginId").val("REAL_PERSON_PLUGIN_ALI_PAY");
        }else{
            $("#pluginId").val(reAuthParams.reAuthType);
        }
        $("#reAuthDec").html(reAuthParams.reAuthDec6);
        $("#faceAuthShowDiv").show();
        $("#userNameDiv").hide();
        $("#dynamicType").hide();
        $("#pwdType").hide();
        $("#questionDiv").hide();
        $("#combinedWxDiv").hide();
        $("#combinedQqDiv").hide();
        $("#otpDiv").hide();
        $("#otpDivBind").hide();
        $("#reAuthDecMobileOtp").hide();
        $("#optUpdateInfoTips").hide();
    }
}
 var showPhoneOrEmail = '';

function mobileCheckUserPhone() {
    //判断用户是否有手机号
    const hasPhone = $("#changePhone_mobileOtp").val();
    const hasEmail = $("#emailValid_mobileOtp").val();
    showPhoneOrEmail="phone";
    // 1. 手机号、邮箱都没有展示没有校验方式提示
    if(!hasPhone && !hasEmail) {
        $('#otp_no_auth_dialog').show();
        return;
    } else if(hasPhone!="" && hasEmail!=""){
        //2. 用户拥有手机号和邮箱展示切换radio
        // 2.1 手机号存在
        $("#validPhoneBox").show();
        $("#validEmailBox").hide();
        $("#phoneRadio input[name='valid']").addClass("common-mobile-radio-checked");
        $("#emailRadio input[name='valid']").removeClass("common-mobile-radio-checked");
    }else if(hasPhone!="" && hasEmail==""){
        // 2.2 手机号存在,邮箱不存在
        $("#validPhoneBox").show();
        $("#otpValidRadioGroup").hide();
        $("#validEmailBox").hide();
        $("#phoneRadio input[name='valid']").addClass("common-mobile-radio-checked");
        $("#emailRadio input[name='valid']").removeClass("common-mobile-radio-checked");
    }else if(hasPhone=="" && hasEmail!=""){
        showPhoneOrEmail="email";
        // 2.3 手机号不存在,邮箱存在
        $("#validPhoneBox").hide();
        $("#otpValidRadioGroup").hide();
        $("#validEmailBox").show();
        $("#phoneRadio input[name='valid']").removeClass("common-mobile-radio-checked");
        $("#emailRadio input[name='valid']").addClass("common-mobile-radio-checked");
    }
    countDownButton($("#getImprovePhoneCodeId_otp"), 0);
    countDownButton($("#getImproveEmailCodeId_otp"), 0);
    $("#checkPhoneCode_mobileOtp").val('');
    $("#checkEmailCode_mobileOtp").val("");
    $('.otp_checkPhone').show();
    $("input[name='otpCode']").each(function(index,item){
        $(this).val('');
    });
    $("#getImprovePhoneCodeId_otp").text(utils.subStr($("#getImprovePhoneCodeId_otp").text(), 5));
    $("#getImproveEmailCodeId_otp").text(utils.subStr($("#getImproveEmailCodeId_otp").text(), 5));
}

function mobileChangeOtherType() {
    $("#changeOtherType").addClass("animationChangeType")
    $('.changeOtherType').show();
}

function mobileChangeTypeCancel() {
    $('.changeOtherType').hide();
}

// otpRadio切换
function handleChangeRadio(val) {
    const ele = val.querySelector('input[type="radio"]');
    showPhoneOrEmail = ele.value;
    ele.classList.add('common-mobile-radio-checked');
    const ohterId = showPhoneOrEmail === "phone" ? "emailRadio" : "phoneRadio";
    const other = document.getElementById(ohterId); 
    other.querySelector('input[type="radio"]').classList.remove('common-mobile-radio-checked');
    if(showPhoneOrEmail === "email") {
        $("#validPhoneBox").hide();
        $("#validEmailBox").show();
        $("#checkPhoneCode_mobileOtp").val('');
    } else {
        $("#validPhoneBox").show();
        $("#validEmailBox").hide();
        $("#checkEmailCode_mobileOtp").val("");
    }
}

//发送验证码.
function getMobileReAuthOtpPhoneCode(that) {
    commonDisabledBtn($(that), true);
    $.ajax({
        type: "POST",
        url: contextPath+"/otp/getDynamicCodeByReAuthOtp.do",
        data: {
            showPhoneOrEmail:showPhoneOrEmail,
        },
        success: function (data) {
            commonDisabledBtn($(that), false);
            if(data.errCode == 1){
                utils.alertBox(data.errMsg);
            }else{
                utils.alertBox(data.errMsg);
            }
            if(showPhoneOrEmail === "email") {
                countDownButton($("#getImproveEmailCodeId_otp"), data.data);
            }else{
                countDownButton($("#getImprovePhoneCodeId_otp"), data.data);
            }
        }
    });
}

// 设置text按钮禁用
function commonDisabledBtn(that, flag){
    if(flag){
        $(that).attr("disabled",true).addClass("common-plain-primary-btn-disabled");
    }else{
        $(that).attr("disabled",false).removeClass("common-plain-primary-btn-disabled");
    }
}

//校验验证码.
function checkMobileReAuthOtpPhoneCode(that) {
    var checkPhoneCode = '';
    if(showPhoneOrEmail=="email"){
        checkPhoneCode = $("#checkEmailCode_mobileOtp").val();
    }else{
        checkPhoneCode = $("#checkPhoneCode_mobileOtp").val();
    }
    if(!checkPhoneCode || checkPhoneCode==''){
        utils.alertBox(reAuthParams.dynamicEmpty);
        return;
    }
    disabledBtn($(that), true);
    $.ajax({
        type: "POST",
        url: contextPath+"/otp/checkDynamicCodeByReAuthOtp.do",
        dataType: "json",
        data: {
            checkPhoneCode:checkPhoneCode,showPhoneOrEmail:showPhoneOrEmail
        },
        success: function (data) {
            if(data.errCode == 1){
                //手机号校验成功，展示otp绑定二维码
                $.ajax({
                    type: "POST",
                    url: contextPath+"/otp/getOtpToken.do",
                    dataType: "json",
                    data: {
                        otpSign:data.data,
                    },
                    success: function (data) {
                        if(data.code == 1){
                            //展示otp绑定二维码
                            $('.otp_checkPhone').hide();
                            $("#otpDiv").hide();
                            $("#otpDivBind").show();
                            // $("#optUpdateInfoTips").hide();
                            if(data.data.otpToken!=''){
                                // var otpToken=decryptPassword(data.data.otpToken,DEFAULT_SALT);
                                var otpToken=atob(data.data.otpToken);
                                $("#otp_bind_secret").html(otpToken.substring(0,10)+"...");
                                $("#otp_bind_secret_hidden").val(otpToken);
                            }
                            $("#mobileOtpBindClockTime").html(data.data.systemTime);
                            startTime("mobileOtpBindClockTime");
                            $("#mobileOtpBindImg").attr("src","data:image/jpeg;base64,"+data.data.otpUrl);
                            clearMobileOtpCode(2);
                        }else{
                            utils.alertBox(data.message);
                        }
                        disabledBtn($(that), false);
                    }
                });
            }else{
                utils.alertBox(data.errMsg);
            }
            disabledBtn($(that), false);
        }
    });
}

// otp校验方式返回
function closeOtpAuthDialog() {
    $('.otp_checkPhone').hide();
}

// 关闭otp绑定没有校验方式弹窗
function closeNoAuthDialog() {
    $("#otp_no_auth_dialog").hide();
}

// 定义登录接口请求变量，用于可信设备参数拆分
let loginParams = {}, curLoginType = "";
function doOtpBindLogin(that){
    var otpCode="";
    if(reAuthParams.reAuthType == "10"){
        $("input[name='otpCode']").each(function(index,item){
            if(item.value!="" && item.value.length!=1){
                utils.alertBox(reAuthParams.otpCodeRequired);
                return;
            }
            otpCode+=item.value;
        });
        if(otpCode=="" ||otpCode.length!=6){
            utils.alertBox(reAuthParams.otpCodeRequired);
            return;
        }
    }
    loginParams = {
        service:reAuthParams.service,
        reAuthType:reAuthParams.reAuthType,
        isMultifactor:reAuthParams.isMultifactor,
        otpCode:otpCode
    }
    curLoginType = reAuthParams.isSleepAccount === '0' ? "login" : "";
    if(reAuthParams.isSleepAccount === '0') {
        // 展示可信设备弹窗
        $(".trust-device-modal").css("display", "block");
    } else {
        // loginParams.skipTmpReAuth = true;
        handleOtpDoLoginInterface(loginParams);
    }
}

// otp请求登录接口方法
function handleOtpDoLoginInterface(params) {
    const that = document.getElementById("reAuthSubmitBtn");
    disabledBtn(that, true);
    $.ajax({
        type: "POST",
        url: contextPath+"/reAuthCheck/reAuthSubmit.do",
        dataType: "json",
        data: params,
        success: function (data) {
            if(data.code == 'reAuth_failed'){
                utils.alertBox(data.msg);
                clearMobileOtpCode(2);
            }else if(data.code == 'reAuth_unauthorized'){
                utils.alertBox(data.msg);
                clearMobileOtpCode(2);
            }else{
                if (reAuthParams.service!=undefined && reAuthParams.service != '') {
                    window.location.href = contextPath+"/login?service="+encodeURIComponent(reAuthParams.service);
                } else {
                    window.location.href = contextPath+"/login";
                }
            }
            disabledBtn(that, false);
        }
    });
}

function doLogin(that){
	var password = "";
	var dynamicCode = "";
	var uuid = "";
	var answer1 = "";
	var answer2 = "";
	var otpCode="";
	if(reAuthParams.reAuthType == "2" || reAuthParams.reAuthType == ""){
		if(!checkRequired($("#password"), reAuthParams.passwordEmpty)){
			return;
		}
		password = encryptPassword($("#password").val().trim(), reAuthParams.pwdEncryptSalt);
	}else if(reAuthParams.reAuthType == "3" || reAuthParams.reAuthType == "4" || reAuthParams.reAuthType == "5"
        || reAuthParams.reAuthType == "11" || reAuthParams.reAuthType == "12" || reAuthParams.reAuthType == "13"){
		if(!checkRequired($("#dynamicCode"), reAuthParams.dynamicEmpty)){
			return;
		}
		dynamicCode = $("#dynamicCode").val().trim();
	}else if(reAuthParams.reAuthType == "6"){
		uuid = $("#uuid").val().trim();
	}else if(reAuthParams.reAuthType == "7"){
		if(!checkRequired($("#answer1"), reAuthParams.answerEmpty)
			|| !checkRequired($("#answer2"), reAuthParams.answerEmpty)){
			return;
		}
		answer1 = $("#answer1").val().trim();
		answer2 = $("#answer2").val().trim();
	}else if(reAuthParams.reAuthType == "10"){
        $("input[name='otpCode']").each(function(index,item){
            if(item.value!="" && item.value.length!=1){
                utils.alertBox(reAuthParams.otpCodeRequired);
                return;
            }
            otpCode+=item.value;
        });
        if(otpCode=="" ||otpCode.length!=6){
            utils.alertBox(reAuthParams.otpCodeRequired);
            return;
        }
    }
    loginParams = {
        service:reAuthParams.service,
        reAuthType:reAuthParams.reAuthType,
        isMultifactor:reAuthParams.isMultifactor,
        password:password,
        dynamicCode:dynamicCode,
        uuid:uuid,
        answer1:answer1,
        answer2:answer2,
        otpCode:otpCode
    };
    curLoginType = reAuthParams.isSleepAccount === '0' ? "login" : "";
    if(reAuthParams.isSleepAccount === '0') {
        // 展示可信设备弹窗
        $(".trust-device-modal").css("display", "block");
    } else {
        // loginParams.skipTmpReAuth = true;
        handleDoLoginInterface(loginParams);
    }
}

// 请求登录接口方法
function handleDoLoginInterface(params) {
    const that = document.getElementById("reAuthSubmitBtn");
    disabledBtn(that, true);
    $.ajax({
        type: "POST",
        url: contextPath+"/reAuthCheck/reAuthSubmit.do",
        dataType: "json",
        data: params,
        success: function (data) {
            if(data.code == 'reAuth_failed'){
            	utils.alertBox(data.msg);
                clearMobileOtpCode(1);
            }else if(data.code == 'reAuth_unauthorized'){
            	utils.alertBox(data.msg);
                clearMobileOtpCode(1);
            }else{
                if (reAuthParams.service!=undefined && reAuthParams.service != '') {
                    window.location.href = contextPath+"/login?service="+encodeURIComponent(reAuthParams.service);
                } else {
                    window.location.href = contextPath+"/login";
                }
            }
            disabledBtn(that, false);
        }
    });
}

/**
 * 打开QQ客户端
 * @param combinedType
 */

// 可信设备拆分参数
let combinedType = "", combReAuthType = "1";
function reAuthByCombined(type) {
    combinedType = type;
    combReAuthType = reAuthParams.isMultifactor=='true' ? '2' : '1';
    curLoginType = reAuthParams.isSleepAccount === '0' ? "url" : "";
    if(reAuthParams.isSleepAccount === '0') {
        // 展示可信设备弹窗
        $(".trust-device-modal").css("display", "block");
    } else {
        handleGoCombined(false);
    }
}

//处理QQ微信客户端
function handleGoCombined(skipTmpReAuth) {
    const skipTmpReAuthStr = skipTmpReAuth === undefined ? "" : "&skipTmpReAuth="+skipTmpReAuth;
    if (reAuthParams.service!=undefined && reAuthParams.service != '') {
        window.location.href = contextPath+"/combinedLogin.do?type="+combinedType+"&reAuth="+combReAuthType+"&success="+encodeURIComponent(reAuthParams.service)+skipTmpReAuthStr;
    } else {
        window.location.href = contextPath+"/combinedLogin.do?type="+combinedType+"&reAuth="+combReAuthType+skipTmpReAuthStr;
    }
}

//实人认证
function reAuthByFaceAuth() {
    if(reAuthParams.isSleepAccount === '0') {
        $(".trust-device-title-box .first-title").html(reAuthParams.trustDeviceAskLine2);
        // 展示可信设备弹窗
        $(".trust-device-modal").css("display", "block");
    } else {
        handleGoFaceAuth(false);
    }
}
function handleGoFaceAuth(skipTmpReAuth) {
    var pluginId = $("#pluginId").val();
    $.ajax({
        url: contextPath + "/faceAuthWithPlugins/getQrCodeById?ts=" + new Date().getTime(),
        data: {pluginId:pluginId},
        dataType: "json",
        success: function (data) {
            if (data.errCode == "1") {
                $.ajax({
                    url: contextPath + "/faceAuthWithPlugins/getRealPersonAuthUrl?ts=" + new Date().getTime(),
                    data: {pluginId:pluginId,uuid:data.uuid,service:reAuthParams.service,isMultiFactor:reAuthParams.isMultifactor,skipTmpReAuthStr:skipTmpReAuth},
                    dataType: "json",
                    success: function (data) {
                        if (data.errCode == "0") {
                            window.location.href = data.faceAuthUrl;
                        }else{
                            utils.alertBox(data.errMsg);
                        }
                    }
                });
            }else{
                utils.alertBox(data.errMsg);
            }
        }
    });
}

//清除otpCode 并且将光标自动定位到输入框
function clearMobileOtpCode(isBind) {
    if(isBind=='1'){
        appBodyContent.clear();
    }else{
        appBodyContent2.clear();
    }
}

//发送验证码.
function sendDynamicCodeByPhone(that) {
	if(reAuthParams.reAuthType == '3'){
		authCodeTypeName = "reAuthDynamicCodeType";
	}else if(reAuthParams.reAuthType == '4'){
		authCodeTypeName = "reAuthWChatDynamicCodeType";
	}else if(reAuthParams.reAuthType == '5'){
		authCodeTypeName = "reAuthCpdailyDynamicCodeType";
    }else if(reAuthParams.reAuthType == '11'){
        authCodeTypeName = "reAuthEmailDynamicCodeType";
    }else if(reAuthParams.reAuthType == '12'){
        authCodeTypeName = "reAuthDingTalkDynamicCodeType";
    }else if(reAuthParams.reAuthType == '13'){
        authCodeTypeName = "reAuthWeLinkDynamicCodeType";
    }
	disabledBtn($(that), true);
    $.ajax({
        type: "POST",
        url: contextPath+"/dynamicCode/getDynamicCodeByReauth.do",
        dataType: "json",
        data: {userName: reAuthParams.reAuthUserId, authCodeTypeName: authCodeTypeName},
        success: function (data) {
            var returnMessage = data.returnMessage;
            disabledBtn($(that), false);
            if (data.res == "success") {
                if(data.mobile !=undefined){
                    returnMessage = returnMessage + data.mobile;
                }
            	countDownButton($("#getDynamicCode"), data.codeTime);
            } else if (data.res == "wechat_success") {
                countDownButton($("#getDynamicCode"), 120);
            } else if (data.res == "cpdaily_success") {
                countDownButton($("#getDynamicCode"), 120);
            }else if(data.res == "code_time_fail"){
            	countDownButton($("#getDynamicCode"), data.codeTime);
            }
            utils.alertBox(returnMessage);
        }
    });
}

function changeMobileReAuthType(that) {
    var reAuthType=$(that).attr("id");
    $.ajax({
        type: "POST",
        url: contextPath+"/reAuthCheck/changeReAuthType.do",
        dataType: "json",
        data: {isMultifactor: reAuthParams.isMultifactor,reAuthType:reAuthType,service:reAuthParams.service},
        success: function (data) {
            if(data.code==1){
                $("#changeReAuthTypeButton").text(data.data.reAuthTypeName);
                reAuthParams.reAuthType = data.data.reAuthType;
                //如果是otp 初始化配置
                if(reAuthParams.reAuthType=="10"){
                    $("#mobileClockTime").html(data.data.systemTime);
                    $("#countryCode_mobileOtp_option").text(data.data.userPhonePre);
                    $("#changePhone_mobileOtp").val(data.data.userPhone);
                    $("#emailValid_mobileOtp").val(data.data.userSecurityEmail);
                    $("input[name='otpCode']").each(function(index,item){
                        $(this).val('');
                    });
                    clearMobileOtpCode(1);
                }
                if(data.data.reAuthUserNameInput) {
                    $('#mobileUserNameShow').text(data.data.reAuthUserNameInput);
                }
                initView();
                if(reAuthParams.reAuthType=="10" && data.data.otpIsBind==false){
                    //展示otp绑定二维码
                    $('.otp_checkPhone').hide();
                    $("#otpDiv").hide();
                    $("#otpDivBind").show();
                    // $("#optUpdateInfoTips").hide();
                    if(data.data.otpToken!=''){
                        // var otpToken=decryptPassword(data.data.otpToken,DEFAULT_SALT);
                        var otpToken=atob(data.data.otpToken);
                        $("#otp_bind_secret").html(otpToken.substring(0,10)+"...");
                        $("#otp_bind_secret_hidden").val(otpToken);
                    }
                    if($("#mobileOtpBindClockTime").text()==''){
                        $("#mobileOtpBindClockTime").html(data.data.systemTime);
                        startTime("mobileOtpBindClockTime");
                    }
                    $("#mobileOtpBindImg").attr("src","data:image/jpeg;base64,"+data.data.otpUrl);
                    clearMobileOtpCode(2);
                }
            }else{
                $(".reauth_error").html(data.message);
            }
            $('.changeOtherType').hide();
        }
    });
}

function otpAccountCopy() {
    //获取要复制的文本
    var text=$("#otp_bind_account").text();
    copyText(text);
}
function otpSecretCopy() {
    //获取要复制的文本
    var text=$("#otp_bind_secret_hidden").val();
    copyText(text);
}

function copyText(text) {//拷贝文本函数
    var oInput = document.createElement('input');//创建一个input标签
    oInput.value = text;//设置value属性
    document.body.appendChild(oInput);//挂载到body下面
    oInput.select(); // 选择对象
    document.execCommand("Copy"); // 执行浏览器复制命令
    oInput.className = 'oInput';
    oInput.style.display='none';
    utils.alertBox(reAuthParams._copyTips);
}

//统一校验必填和展示错误信息的方法
function checkRequired(that, errorMsg) {
	if($(that).val().trim() == ''){
	    //移动端不需要必填样式
		// $(that).parent().addClass("required");
		utils.alertBox(errorMsg);
		return false;
	}
	return true;
}

//随机获取2个安全问题
function getRandomQuestion(){
	$.ajax({
        type: "POST",
        url: contextPath+"/reAuthCheck/getRandomQuestion.do",
        dataType: "json",
        data: {},
        success: function (data) {
            $("#questionDiv1").html(data.question1);
            $("#questionDiv2").html(data.question2);
        }
    });
}

//动态码发送后倒计时函数
var timeFlag;
function countDownButton(obj, second) {
	// 如果秒数还是大于0，则表示倒计时还没结束
    if (second > 0) {
    	disabledBtn(obj, true)
    	$(obj).text(second + "s");// 按钮里的内容呈现倒计时状态
        second--;// 时间减一
        timeFlag = setTimeout(function() {
        	countDownButton(obj, second);
    	}, 1000);
    } else {
    	// 否则，按钮重置为初始状态
    	disabledBtn(obj, false)
        $(obj).text(reAuthParams.getCodeName);// 按钮里的内容恢复初始状态
        clearInterval(timeFlag);//停止定时任务
    }
}

var btnColor = {};
function disabledBtn(that, flag){
	if(flag){
		//按钮置灰不可点击
		$(that).attr("disabled",true).addClass("disabled_btn");
	}else{
		$(that).attr("disabled",false).removeClass("disabled_btn");
	}
}

//刷新认证方式
function refreshType(){
	$.ajax({
        type: "POST",
        url: contextPath+"/reAuthCheck/refreshReAuthType.do",
        dataType: "json",
        data: {usedType: reAuthParams.reAuthType, isMultifactor: reAuthParams.isMultifactor,service:reAuthParams.service},
        success: function (data) {
        	$("*").removeClass("required");
        	if(data.reAuthType){
        		reAuthParams.reAuthType = data.reAuthType
            	initView();
        	}else{
        		utils.alertBox(requestTimeOut);
        	}
        }
    });
}

//点击更改密码可见状态
var canSee = false;
function changePwdSee() {
    if (!canSee) {
        $('#password')[0].type = 'text';
    } else {
        $('#password')[0].type = 'password';
    }
    $('.icon-visibility').toggle();
    $('.icon-visibilityoff').toggle();
    canSee = !canSee;
}

function startTime(id) {
    try {
        var clockTime=$("#"+id);
        if(clockTime && clockTime!=''){
            var systemTime = clockTime.html();
            if(systemTime && systemTime!=""){
                var cacheTime = new Date(Date.parse(systemTime.replace(/-/g, "/")));
                var date=new Date(cacheTime.getTime() + 1000);
                var year = date.getFullYear(); //当前年份
                var month = date.getMonth(); //当前月份
                var data = date.getDate(); //天
                var hours = date.getHours(); //小时
                var minute = date.getMinutes(); //分
                var second = date.getSeconds(); //秒
                var time = year + "-" + checkTime((month + 1)) + "-" + checkTime(data) + " " + checkTime(hours) + ":" + checkTime(minute) + ":" + checkTime(second);
                clockTime.html(time);
            }
        }
        setTimeout("startTime('"+id+"')", 1000); //每一秒中重新加载startTime()方法
    }catch (e){

    }
}
//补位 当某个字段不是两位数时补0
function checkTime(str) {
    var num;
    str >= 10 ? num = str : num = "0" + str;
    return num;
}

// 展示绑定指南弹窗
$(document).on('click', '.otp_reauth_bind_guide_secret',() => {
    $(".bind-guide-modal").css("display", "block");
    $(".guide-key-img").css("display", "block");
    $(".guide-img-box").scrollTop(0);
})

$(document).on('click', '.otp_reauth_bind_guide_qrcode',() => {
    $(".bind-guide-modal").css("display", "block");
    $(".guide-qrcode-img").css("display", "block");
    $(".guide-img-box").scrollTop(0);
})

// 隐藏绑定指南弹窗
$(document).on('click', '.guide-close',() => {
    $(".bind-guide-modal").css("display", "none");
    $(".guide-key-img").css("display", "none");
    $(".guide-qrcode-img").css("display", "none");
})

// 处理仅本次登录
$(document).on('click', '.trust-cancel-btn',function () {
    $(".trust-device-modal").css("display", "none");
    $(".trust-device-title-box .first-title").html(reAuthParams.trustDeviceAskLine);
    if(reAuthParams.reAuthType == "6" || reAuthParams.reAuthType == "18"
        || reAuthParams.reAuthType.indexOf("real_person_plugin_") !== -1 || reAuthParams.reAuthType.indexOf("REAL_PERSON_PLUGIN_") !== -1){
        handleGoFaceAuth(false);
    }else{
        if(curLoginType === "login") {
            loginParams.skipTmpReAuth = false;
            if(loginParams.reAuthType === "10") {
                handleOtpDoLoginInterface(loginParams);
            } else {
                handleDoLoginInterface(loginParams);
            }
        } else {
            handleGoCombined(false);
        }
    }
})

// 处理信任此设备
$(document).on('click', '.trust-con-btn',function() {
    $(".trust-device-modal").css("display", "none");
    $(".trust-device-title-box .first-title").html(reAuthParams.trustDeviceAskLine);
    if(reAuthParams.reAuthType == "6" || reAuthParams.reAuthType == "18"
        || reAuthParams.reAuthType.indexOf("real_person_plugin_") !== -1 || reAuthParams.reAuthType.indexOf("REAL_PERSON_PLUGIN_") !== -1){
        handleGoFaceAuth(true);
    }else{
        if(curLoginType === "login") {
            loginParams.skipTmpReAuth = true;
            if(loginParams.reAuthType === "10") {
                handleOtpDoLoginInterface(loginParams);
            } else {
                handleDoLoginInterface(loginParams);
            }
        } else {
            handleGoCombined(true);
        }
    }
})
