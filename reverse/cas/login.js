//登录常量
var LOGIN_PWD_USERNAME_ID = ".pwd_login > * > #username";
var LOGIN_PWD_PASSWORD_ID = ".pwd_login > * > #password";
var LOGIN_PWD_CAPTCHA_ID = ".pwd_login > * > #captcha";
var LOGIN_TEL_USERNAME_ID = ".tel_login > * > #username";
var LOGIN_TEL_DYNAMICCODE_ID = ".tel_login > * > #dynamicCode";
var LOGIN_TEL_CAPTCHA_ID = ".tel_login > * > #captcha";

var LOGIN_SUBMIT_ID = '#login_submit';
var DEFAULT_SALT = "rjBFAaHsNkKAhpoi";
var COMBINE_OPTIONS = ['wechat', 'qq', 'weibo', 'combine'];
var COMBINE_OPTIONS_CLASS = ['wechat_item', 'qq_item', 'weibo_item', 'combine_item'];

var excludeRegular = window.excludeRegular === undefined ? "用户名包含特殊字符！" : excludeRegular;
var reg = window.reg === undefined ? "" : reg;
$(function () {
	//切换用户名密码登录和动态码登录
	if (type == 'dynamicLogin' || cllt == 'dynamicLogin') {
		//短信网关没开不展示动态码登录
		if (is_dynamicLogin != "true" || !is_dynamicLogin) {
			window.location.href = contextPath;
			return;
		}
		//动态码登录
		$("#phoneLoginDiv").show();
		$("#pwdLoginDiv").remove();
		$("#accountTab").show();
		$("#mobileTab").hide();
		reloadCaptcha(true);
	} else {
		//用户名密码登录
		$("#pwdLoginDiv").show();
		$("#phoneLoginDiv").remove();
		$("#accountTab").hide();
		//短信网关没开不展示动态码登录
		if (is_dynamicLogin != "true" || !is_dynamicLogin) {
			$("#mobileTab").remove();
		}
		checkNeedCaptcha();
		showCaptchaOnLoad();
	}
	//检查浏览器是否支持fido 以及是否绑定了fido信息
	let openFlag = window.localStorage.getItem("anonbiometricso");
	let fidoUserId = window.localStorage.getItem("anonbiometricsu");
	if (openFlag != "true" || !openFlag || !fidoUserId || !isDeviceBinded()) {
		//排除手动url添加type
		if (type == 'fidoLogin') {
			window.location.href = contextPath;
			return;
		}
		$(".fido_item").remove();
		$("#fidoLoginDiv").remove();
		if ($(".login_ways").children().length == 0) {
			$(".combine_options_footer").remove();
		}
	}
	//input禁止自动填入
	reloadInput();

	// 元素聚焦
	if ($(LOGIN_PWD_USERNAME_ID).val() != "") {
		$(LOGIN_PWD_PASSWORD_ID).focus();
	} else {
		$(LOGIN_PWD_USERNAME_ID).focus();
	}

	//失去焦点判断用户名特殊字符、是否需要验证码
	$(LOGIN_PWD_USERNAME_ID).focusout(function (e) {
		checkSpecificKey($(LOGIN_PWD_USERNAME_ID).val());
		checkNeedCaptcha();
	});

	//enter提交
	$('body').on('keyup', function (event) {
		if (event.keyCode == 13) {
			$(LOGIN_SUBMIT_ID).click();
		}
	});
	$(LOGIN_SUBMIT_ID).click(function () {
		if (!$("#agreeProtocol").prop("checked")) {
			utils.alertBox("请先确认《隐私协议》内容。");
			return
		}

		// 点击之后即将按钮置为不可用
		utils.disabledBtn(this, true);
		if (checkForm()) {
			var cllt = $("#cllt").val();
			if (needCaptcha && captchaSwitch == "2" && cllt == "userNameLogin") {
				createSliderCaptcha();
				utils.disabledBtn(this, false);
			} else {
				$("#loginFromId").submit();
			}
		} else {
			utils.disabledBtn(this, false);
		}
	});

	// 显示文本框清除
	$('.form input').bind('input propertychange', function () {
		if ($(this).val().trim()) {
			$(this).siblings('.input_del').show();
		} else {
			$(this).siblings('.input_del').hide();
		}
		//setBtnColor()
	});

	// input文本框失焦
	$('.form input').blur(function () {
		$(this).siblings('.input_del').hide();
		setTimeout(function () {
			window.scrollTo(0, 0)
		}, 100)
	}).focus(function () {
		if ($(this).val().trim()) {
			$(this).siblings('.input_del').show()
		}
		var clientHeight = document.documentElement.clientHeight || document.body.clientHeight
		var offsetTop = $(this).offset().top - clientHeight / 4
		setTimeout(function () {
			window.scrollTo(0, offsetTop)
		}, 100)
	})
	// input文本框清除文本事件
	$('.input_del').on('mousedown', function () {
		$(this).siblings('input').val('');
		$(this).siblings('.login-inputuser-tip').hide();
		// setBtnColor()
	});

	// input pssword
	$('.input_eye').on('click', function () {
		if ($(this).hasClass('input_eye_hide')) {
			$(this).removeClass('input_eye_hide').addClass('input_eye_show')
			$("#password").prop('type', 'text');
		} else {
			$(this).removeClass('input_eye_show').addClass('input_eye_hide')
			$("#password").prop('type', 'password')
		}
	});

	var tipWidth = document.body.clientWidth - 100 + "px";
	// 点击登录说明提示
	$(".onlineGuider").click(function () {
		if ($("#online-guide-tip").css('display') == 'none') {
			$("#online-guide-tip").show();
			$("#online-guide-tip").css({'width': tipWidth})
		} else {
			$("#online-guide-tip").hide();
		}

	});

	//发送手机动态码
	$('#getTelCode').click(function () {
		if (utils.toastRequireInput($(LOGIN_TEL_USERNAME_ID), 0, 100, inputMobileTip)) {
			return false
		}
		if (captchaSwitch == "2") {
			createSliderCaptcha();
		} else {
			getDynamicCode();
		}
	});

	//1、如果错误提示信息过长就截取
	var showErrorText = $("#formErrorTip2").text();
	if (!utils.isEmptyStr(showErrorText)) {
		utils.alertBox(showErrorText);
	}

	$('input').bind('input propertychange', function () {
		if ($(this).val()) {
			// $(this).prev().css('left', 50 + $(this).val().pxWidth() + 'px')
			$(this).prev().show()
			$(this).css('padding', '25px 0 10px 40px')
		} else {
			$(this).prev().hide()
			$(this).css('padding', '10px 0 10px 40px')
		}
	})


  //解决H5页面应软键盘把背景图顶上去问题
  var height = document.documentElement.clientHeight
  window.onresize = function () {
    $('.mBg').css('height', height + 'px')
  }
})

String.prototype.pxWidth = function (font) {
	// re-use canvas object for better performance
	var canvas = String.prototype.pxWidth.canvas || (String.prototype.pxWidth.canvas = document.createElement("canvas")),
		context = canvas.getContext("2d");

	font && (context.font = font);
	var metrics = context.measureText(this);

	return metrics.width;
}

function credentialsCount() {
	//判断验证码是否一直显示
	try {
		if (badCredentialsCount && badCredentialsCount == 0) {
			return true;
		}
	} catch (err) {
		console.log('捕获到异常：', err);
	}
	return false;
}

function showCaptchaOnLoad() {
	if (credentialsCount()) {
		reloadCaptcha(true);
	}
}

// 禁用掉登录按钮
function disableLoginBtn() {
	$(LOGIN_SUBMIT_ID).attr('disabled', true)
}

// 恢复登录按钮
function recoverLoginBtn() {
	$(LOGIN_SUBMIT_ID).attr('disabled', false)
}

// 登录前校验
function checkForm() {
	var cllt = $("#cllt").val();
	if (cllt == 'userNameLogin') {
		if (utils.toastRequireInput($(LOGIN_PWD_USERNAME_ID), 0, 100, inputUserNameTip)
			|| utils.toastRequireInput($(LOGIN_PWD_PASSWORD_ID), 0, 32, inputPasswordTip)) {
			recoverLoginBtn();
			return false
		}
		if (checkSpecificKey($(LOGIN_PWD_USERNAME_ID).val())) {
			return false;
		}
		if (needCaptcha && captchaSwitch == "1" && utils.toastRequireInput($(LOGIN_PWD_CAPTCHA_ID), 0, 10, inputCodeTip)) {
			recoverLoginBtn();
			return false
		}
		$("#saltPassword").val(encryptPassword($(LOGIN_PWD_PASSWORD_ID).val(), $("#pwdEncryptSalt").val()));
		$(LOGIN_PWD_PASSWORD_ID).attr("disabled", "disabled");
	} else {
		if (utils.toastRequireInput($(LOGIN_TEL_USERNAME_ID), 0, 100, inputMobileTip)
			|| utils.toastRequireInput($(LOGIN_TEL_DYNAMICCODE_ID), 0, 10, inputDynamicTip)) {
			recoverLoginBtn();
			return false
		}
		if (checkSpecificKey($(LOGIN_TEL_USERNAME_ID).val())) {
			return false;
		}
	}
	return true
}

// 校验是否需要验证码
function checkNeedCaptcha() {
	var username = $(LOGIN_PWD_USERNAME_ID).val().trim();
	if (username === "") {
		return;
	}
	$.ajax(contextPath + '/checkNeedCaptcha.htl', {
		data: { username: username },
		cache: false,
		dataType: 'json',
		success: function (data) {
			if (data.isNeed) {
				needCaptcha = true;
			} else {
				needCaptcha = false;
			}
			if (credentialsCount()) {
				if ($("#captchaDiv").css("display") == "none") {
					reloadCaptcha(needCaptcha);
				}
			} else {
				reloadCaptcha(needCaptcha);
			}
		}
	})
}

//重新载入验证码
function reloadCaptcha(isNeed) {
	if (isNeed && captchaSwitch == "1") {
		$("#captchaDiv").show();
		$("#captchaImg").attr("src", contextPath + "/getCaptcha.htl?" + new Date().getTime());
	} else {
		// 如果不需要验证码，那么清空
		$("#captcha").val("");
		$("#captchaDiv").hide();
	}
}

// 发送动态码
function getDynamicCode() {
	if (utils.toastRequireInput($(LOGIN_TEL_USERNAME_ID), 0, 100, inputMobileTip)) {
		return false
	}
	if (captchaSwitch == "1" && utils.toastRequireInput($(LOGIN_TEL_CAPTCHA_ID), 0, 10, inputCodeTip)) {
		return false
	}

	var mobile = encryptPassword($(LOGIN_TEL_USERNAME_ID).val(), DEFAULT_SALT);
	var captcha = $(LOGIN_TEL_CAPTCHA_ID).val();
	$.ajax(contextPath + '/dynamicCode/getDynamicCode.htl', {
		data: { mobile: mobile, captcha: captcha },
		cache: false,
		dataType: 'json',
		type: 'POST',
		success: function (data) {
			if (data.code == "error") {
				$(LOGIN_TEL_CAPTCHA_ID).val('');
				reloadCaptcha(true);
			} else if (data.code == 'captchaError') {
				$(LOGIN_TEL_CAPTCHA_ID).val('');
				reloadCaptcha(true);
			} else if (data.code == 'timeExpire' && !utils.isEmptyStr(data.time)) {
				$(LOGIN_TEL_CAPTCHA_ID).val('');
				getTimes(data.time);
			} else if (data.code == 'success') {
				getTimes(120);
			}
			utils.alertBox(data.message);
		}
	})
}

// 倒计时
function getTimes(time) {
	var getCode = $('#getTelCode');
	if (time === 0) {
		reloadCaptcha(true);
		time = 120;
		getCode.text(inputDynamicGetCode);
		getCode.removeClass('disabled');
		$("#getTelCode").css({ "pointer-events": "auto", "background": "#4DAAF5" });
		return
	} else {
		time--;
		getCode.text(time + 's');
		getCode.addClass('disabled');
		$("#getTelCode").css({ "pointer-events": "none", "background": "#ACCDF4" });
	}
	setTimeout(function () {
		getTimes(time)
	}, 1000)
}

function phoneFormation(phoneNumber) {
	return /^\d{11}$/.test(phoneNumber)
}

function showCombineOptions(options) {
	var index = 0
	for (var i = 0; i < COMBINE_OPTIONS.length; i++) {
		if (options.indexOf(COMBINE_OPTIONS[i]) > 0) {
			$('.' + COMBINE_OPTIONS_CLASS[i]).show()
			index++
		}
	}
	if (index > 0) {
		$('.combine_options_footer').show()
	}
}

function setBtnColor() {
	// 失焦之后，判断登录按钮颜色是否需要变成深色可点击的蓝色
	// 1.密码登录判断
	var $btn = $(LOGIN_SUBMIT_ID)
	if ($('.pwd_login').css('display') == 'block') {
		if ($('#username').val() && $('#password').val()) {
			$btn.addClass('btn_complete')
		} else {
			$btn.removeClass('btn_complete')
		}
	} else {
		// 2.短信验证码登录判断
		if ($('#mobile').val() && $('#dynamicCode').val()) {
			$btn.addClass('btn_complete')
		} else {
			$btn.removeClass('btn_complete')
		}
	}
}

// TODO
function showSchoolWay() {
	var strs = [
		{
			name: '北京大学',
			url: 'www.baidu.com'
		},
		{
			name: '北京工商大学',
			url: 'www.baidu.com'
		}
	]
	var actions = []
	var actionItem = {}
	for (var i in strs) {
		actionItem = {
			text: strs[i].name,
			className: 'school_way',
			onClick: function () {
				alert(strs[i].url)
			}
		}
		actions.push(actionItem)
	}

	$.actions({
		title: '学校联合登录',
		actions: actions
	})
}
//加载滑块验证码
function createSliderCaptcha() {
	$.ajax({
		url: contextPath + "/common/toSliderCaptcha.htl",
		type: 'get',
		data: {},
		success: function (data) {
			$("#captchaDiv").hide();
			$("#sliderCaptchaDiv").html(data);
		}
	})
}
//用户名校验特殊字符
function checkSpecificKey(input) {
	if (reg && reg != "") {
		var pattern = new RegExp(reg);
		if (pattern.test(input)) {
			utils.alertBox(excludeRegular, 3000);
			return true;
		} else {
			return false;
		}
	}
	return false;
}